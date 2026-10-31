"""HiBTV 独立生图链路。

这里只处理短剧资产与可选分镜图，不经过电商商品保真提示词、电商负向词或
``advimage.inject_image_job``。工作流文件独立，输入图片一律按
``<image1>``、``<image2>`` ... 的角色被写入 Qwen Image 2.1。
"""
import asyncio
import json
import logging
import random
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import httpx

from ..config import HIBTV_DIR, settings
from . import comfyui

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "workflows" / "hibtv_image_api.json"
PROMPT_NODE = "490"
NEGATIVE_NODE = "491"
ENCODE_NODE = "474"
LATENT_NODE = "456"
SAMPLER_NODE = "458"
SAVE_NODE = "461"
FIRST_IMAGE_NODE = 470
EXTRA_IMAGE_BASE = 620

NEGATIVE = (
    "contact sheet, collage, split screen, split frame, multi-panel, multiple panels, "
    "image grid, 2x2 grid, nine-grid, side-by-side panels, picture-in-picture, diptych, triptych, "
    "character sheet, model sheet, three views in one image, several photos in one image, "
    "duplicate person, cloned face, identical twins, repeated subject, mirrored subject, "
    "extra person, extra people, crowd not requested, text, subtitle, watermark, logo, caption, "
    "border, frame"
)

_LOCK: Optional[asyncio.Lock] = None
_PROBE: Optional[bool] = None   # None=未探测；True=允许纯文本；False=必须带参考图


def worker_url() -> str:
    return (settings.hibtv_image_worker or settings.advideo_image_worker or "").strip()


def available() -> bool:
    if settings.mock_comfy:
        return True
    return bool(worker_url()) and TEMPLATE_PATH.is_file()


def status() -> Dict:
    return {
        "available": available(),
        "mock": bool(settings.mock_comfy),
        "worker": worker_url(),
        "template": str(TEMPLATE_PATH),
        "template_exists": TEMPLATE_PATH.is_file(),
        "text_only": _PROBE,
        "size": [int(settings.hibtv_image_width), int(settings.hibtv_image_height)],
        "degraded": False,
        "textOnlyFallback": _PROBE is False,
        "note": ("纯文本资产在节点要求参考图时使用空白底图；"
                 "分镜图与派生视图始终使用真实参考图"),
        "mode": "hibtv",
    }


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def _gap_sec() -> float:
    try:
        return float(settings.hibtv_image_gap_sec)
    except (TypeError, ValueError):
        return 5.0


async def probe_text_only() -> bool:
    """探测 HiBTV 工作流所用节点是否允许不带参考图片。"""
    global _PROBE
    if _PROBE is not None:
        return _PROBE
    url = worker_url()
    if not url:
        _PROBE = False
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{url.rstrip('/')}/object_info/TextEncodeQwenImage21")
            data = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        _PROBE = False
        return False
    info = (data or {}).get("TextEncodeQwenImage21") or {}
    required = (info.get("input") or {}).get("required") or {}
    optional = (info.get("input") or {}).get("optional") or {}
    _PROBE = not any(str(key).startswith("images") for key in required) and not any(
        str(key).startswith("images") for key in optional
    )
    logger.info("HiBTV 生图：纯文本可用=%s（required=%s）", _PROBE, list(required))
    return _PROBE


def write_blank_png(path: Path, width: int = 512, height: int = 512) -> Path:
    """写一张纯白 PNG，只在节点强制要求至少一张输入图时使用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = max(8, int(width)), max(8, int(height))
    raw = b"".join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def pad_to_grid(path: Path, width: int, height: int) -> Path:
    """把结果落到目标画布；只裁边或补边，不缩放主体。"""
    target = (max(8, int(width)), max(8, int(height)))
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        logger.warning("未安装 Pillow，分镜图不做画布对齐：%s", path)
        return path
    try:
        with Image.open(path) as raw:
            image = raw.convert("RGB")
            if image.size == target:
                return path
            iw, ih = image.size
            tw, th = target
            if iw > tw or ih > th:
                box = (
                    (iw - min(iw, tw)) // 2,
                    (ih - min(ih, th)) // 2,
                    (iw + min(iw, tw)) // 2,
                    (ih + min(ih, th)) // 2,
                )
                image = image.crop(box)
                iw, ih = image.size
            canvas = Image.new("RGB", target)
            left = (tw - iw) // 2
            top = (th - ih) // 2
            canvas.paste(image, (left, top))
            if left:
                canvas.paste(image.crop((0, 0, 1, ih)).resize((left, ih)), (0, top))
                canvas.paste(
                    image.crop((iw - 1, 0, iw, ih)).resize((tw - iw - left, ih)),
                    (left + iw, top),
                )
            if top:
                canvas.paste(canvas.crop((0, top, tw, top + 1)).resize((tw, top)), (0, 0))
                canvas.paste(
                    canvas.crop((0, top + ih - 1, tw, top + ih)).resize((tw, th - ih - top)),
                    (0, top + ih),
                )
            canvas.save(path, "PNG")
    except (OSError, ValueError):
        logger.warning("分镜图画布对齐失败，保留原图：%s", path, exc_info=True)
    return path


def load_template() -> dict:
    if not TEMPLATE_PATH.is_file():
        raise RuntimeError(f"HiBTV 生图工作流缺失：{TEMPLATE_PATH}")
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _inject(
    prompt: str,
    image_names: Sequence[str],
    *,
    width: int,
    height: int,
    seed: int,
    prefix: str,
    negative: str,
    text_only: bool,
) -> dict:
    workflow = load_template()
    workflow[PROMPT_NODE]["inputs"]["value"] = prompt
    workflow[NEGATIVE_NODE]["inputs"]["value"] = negative[:4000]
    workflow[LATENT_NODE]["inputs"].update(
        {"width": int(width), "height": int(height), "batch_size": 1}
    )
    workflow[SAMPLER_NODE]["inputs"]["seed"] = int(seed)
    workflow[SAVE_NODE]["inputs"]["filename_prefix"] = prefix
    encode = workflow[ENCODE_NODE]["inputs"]
    for key in [key for key in encode if key.startswith("images.image_")]:
        del encode[key]

    names = [str(name) for name in image_names if name]
    if text_only and not names:
        return workflow
    for index, name in enumerate(names):
        node_id = str(FIRST_IMAGE_NODE if index == 0 else EXTRA_IMAGE_BASE + index - 1)
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        encode[f"images.image_{index + 1}"] = [node_id, 0]
    return workflow


def _prune(workflow: dict, sink: str) -> dict:
    keep: set[str] = set()
    stack = [sink]
    while stack:
        node_id = stack.pop()
        if node_id in keep or node_id not in workflow:
            continue
        keep.add(node_id)
        for value in (workflow[node_id].get("inputs") or {}).values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in workflow
            ):
                stack.append(value[0])
    return {key: value for key, value in workflow.items() if key in keep}


async def generate_image(
    *,
    prompt: str,
    out_path: Path,
    ref_path: Optional[Path] = None,
    ref_paths: Optional[Sequence[Path]] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    negative: str = "",
    keep_adv_negative: bool = False,
    grid: bool = False,
) -> Dict:
    """执行一次独立的 HiBTV 生图。

    ``keep_adv_negative`` 仅为兼容旧调用保留，HiBTV 不再合并电商负向词。
    """
    del keep_adv_negative
    w = int(width or settings.hibtv_image_width)
    h = int(height or settings.hibtv_image_height)
    refs = [
        Path(path)
        for path in (
            list(ref_paths) if ref_paths else ([ref_path] if ref_path else [])
        )
        if path
    ]
    missing = [str(path) for path in refs if not path.is_file()]
    if missing:
        raise RuntimeError(f"HiBTV 生图引用文件缺失：{missing[:3]}")
    if settings.mock_comfy:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_blank_png(out_path, w, h)
        return {"path": str(out_path), "degraded": False, "seed": 0, "mock": True}
    if not available():
        raise RuntimeError(
            "HiBTV 生图未配置：HIBTV_IMAGE_WORKER / ADVIDEO_IMAGE_WORKER 为空或工作流缺失"
        )

    text_only = False if refs else await probe_text_only()
    client = comfyui.ComfyUIClient(worker_url())
    seed = random.randint(0, 2**63 - 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    negative_text = ", ".join(part for part in (negative.strip(), NEGATIVE) if part)

    async with _lock():
        names = [await client.upload_image(path) for path in refs]
        if not names and not text_only:
            blank = HIBTV_DIR / "_blank" / f"hibtv_blank_{w}x{h}.png"
            if not blank.exists():
                write_blank_png(blank, w, h)
            names = [await client.upload_image(blank)]
        workflow = _inject(
            prompt,
            names,
            width=w,
            height=h,
            seed=seed,
            prefix=f"hibtv_{seed}",
            negative=negative_text,
            text_only=bool(text_only and not refs),
        )
        try:
            prompt_id = await client.submit(_prune(workflow, SAVE_NODE))
            info = await client.wait_image_result(prompt_id)
        except comfyui.ComfyUIError:
            if refs or not text_only:
                raise
            global _PROBE
            _PROBE = False
            blank = HIBTV_DIR / "_blank" / f"hibtv_blank_{w}x{h}.png"
            if not blank.exists():
                write_blank_png(blank, w, h)
            names = [await client.upload_image(blank)]
            workflow = _inject(
                prompt,
                names,
                width=w,
                height=h,
                seed=seed,
                prefix=f"hibtv_{seed}",
                negative=negative_text,
                text_only=False,
            )
            prompt_id = await client.submit(_prune(workflow, SAVE_NODE))
            info = await client.wait_image_result(prompt_id)
            text_only = False

        content = await client.fetch_file(
            info["filename"], info.get("subfolder", ""), info.get("type", "output")
        )
        out_path.write_bytes(content)
        if grid:
            pad_to_grid(out_path, w, h)
        gap = _gap_sec()
        if gap > 0:
            await asyncio.sleep(gap)
    return {"path": str(out_path), "degraded": not text_only and not refs, "seed": seed}


async def generate_batch(jobs: Sequence[Dict]) -> List[Dict]:
    results: List[Dict] = []
    for job in jobs:
        try:
            results.append(await generate_image(**job))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HiBTV 生图失败（%s）：%s", job.get("out_path"), exc)
            results.append({"path": "", "error": str(exc)})
    return results


def describe() -> str:
    return json.dumps(status(), ensure_ascii=False)

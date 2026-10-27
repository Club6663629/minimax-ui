"""短剧工作台：锚点 / 派生视图出图（复用 advideo 的 qwen21 节点与模板）。

复用点与改造：
- 模板仍是 `server/workflows/advideo_image_api.json`（qwen21 i2i）；
- 复用 `advimage.inject_image_job` 的节点注入，但**关掉 PE 开关（484）**——
  短剧的提示词由 deepseek 写好后直写 474，不需要节点侧再改写一遍；
- 出图分两种：`t2i`（纯文本，无参考图）与 `i2i`（带参考图，用于「改图」与派生视图）。

纯 t2i 是否可行取决于节点侧 `TextEncodeQwenImage21.images` 是否必填：
首次调用前探 `/object_info`，必填则退化成本地生成一张纯白参考图（视觉上等价于 t2i），
并把 `degraded=True` 记进 job payload，UI 明示降级。
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
from . import advimage, comfyui

logger = logging.getLogger(__name__)

_LOCK: Optional[asyncio.Lock] = None
_PROBE: Optional[bool] = None   # None=未探测；True=允许纯文本；False=必须带参考图


def worker_url() -> str:
    return (settings.hibtv_image_worker or settings.advideo_image_worker or "").strip()


def available() -> bool:
    if settings.mock_comfy:
        return True
    return bool(worker_url()) and advimage.TEMPLATE_PATH.exists()


def status() -> Dict:
    return {
        "available": available(),
        "mock": bool(settings.mock_comfy),
        "worker": worker_url(),
        "template": str(advimage.TEMPLATE_PATH),
        "template_exists": advimage.TEMPLATE_PATH.exists(),
        "text_only": _PROBE,
        "size": [int(settings.hibtv_image_width), int(settings.hibtv_image_height)],
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
    """探测 `TextEncodeQwenImage21` 是否允许不给参考图。探测失败按「必须给图」处理。"""
    global _PROBE
    if _PROBE is not None:
        return _PROBE
    url = worker_url()
    if not url:
        _PROBE = False
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url.rstrip('/')}/object_info/TextEncodeQwenImage21")
            data = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        _PROBE = False
        return False
    info = (data or {}).get("TextEncodeQwenImage21") or {}
    required = (info.get("input") or {}).get("required") or {}
    optional = (info.get("input") or {}).get("optional") or {}
    _PROBE = not any(str(k).startswith("images") for k in required) and not any(
        str(k).startswith("images") for k in optional)
    logger.info("短剧出图：纯文本可用=%s（required=%s）", _PROBE, list(required))
    return _PROBE


def write_blank_png(path: Path, width: int = 512, height: int = 512) -> Path:
    """生成一张纯白 PNG（零依赖，用 zlib + struct 手写）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = max(8, int(width)), max(8, int(height))
    raw = b"" .join(b"\x00" + b"\xff" * (w * 3) for _ in range(h))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path.write_bytes(png)
    return path


def _inject(prompt: str, image_name: str, *, width: int, height: int, seed: int,
            prefix: str, text_only: bool) -> dict:
    """装配一次出图：关掉 PE（484），提示词直写 474。"""
    wf = advimage.inject_image_job(
        advimage.load_template(),
        scenario=prompt,
        product_name=image_name,
        ref_name="",
        prefix=prefix,
        seed=seed,
        width=width,
        height=height,
        batch_size=1,
        resolution=int(getattr(settings, "advideo_pe_resolution", 0) or 0),
        fidelity_pe=False,
        canvas_follow_ratio=True,
    )
    # 关 PE：474.prompt 直接吃 484.on_false，也就是我们的提示词原文
    wf[advimage.NODE_SWITCH]["inputs"]["switch"] = False
    wf[advimage.NODE_SWITCH]["inputs"]["on_false"] = prompt
    enc = wf[advimage.NODE_ENCODE]["inputs"]
    if text_only:
        for key in [k for k in enc if k.startswith("images.image_")]:
            del enc[key]
        wf[advimage.NODE_BATCH]["inputs"] = {"images.image1": [advimage.NODE_LOAD_PRODUCT, 0]}
    return wf


async def generate_image(
    *,
    prompt: str,
    out_path: Path,
    ref_path: Optional[Path] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Dict:
    """出一张图并落到 out_path。返回 {path, degraded, seed}。"""
    w, h = int(width or settings.hibtv_image_width), int(height or settings.hibtv_image_height)
    if settings.mock_comfy:
        # Mock 模式：写一张占位图，让「锚点 → 派生 → 引用素材」整条链路可离线验证
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_blank_png(out_path, w, h)
        return {"path": str(out_path), "degraded": False, "seed": 0, "mock": True}
    if not available():
        raise RuntimeError(
            "短剧出图未配置：HIBTV_IMAGE_WORKER / ADVIDEO_IMAGE_WORKER 为空或模板缺失"
        )
    text_only = False if ref_path else await probe_text_only()
    client = comfyui.ComfyUIClient(worker_url())
    seed = random.randint(0, 2 ** 63 - 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with _lock():
        # 参考图：改图 / 派生视图用真实图；纯文本路径不需要（退化时用白底图）
        img_name = ""
        if ref_path:
            img_name = await client.upload_image(Path(ref_path))
        elif not text_only:
            blank = HIBTV_DIR / "_blank" / f"blank_{w}x{h}.png"
            if not blank.exists():
                write_blank_png(blank, w, h)
            img_name = await client.upload_image(blank)

        wf = _inject(prompt, img_name or "", width=w, height=h, seed=seed,
                     prefix=f"hibtv_{seed}", text_only=bool(text_only and not ref_path))
        try:
            prompt_id = await client.submit(advimage._prune(wf, advimage.NODE_SAVE))  # noqa: SLF001
            info = await client.wait_image_result(prompt_id)
        except comfyui.ComfyUIError as exc:
            if not ref_path and text_only:
                # 探测说可以纯文本、实际不行：立刻退化成本地白底参考图再试一次
                logger.warning("短剧出图纯文本被节点拒绝，退化白底参考图重试：%s", exc)
                global _PROBE
                _PROBE = False
                blank = HIBTV_DIR / "_blank" / f"blank_{w}x{h}.png"
                if not blank.exists():
                    write_blank_png(blank, w, h)
                img_name = await client.upload_image(blank)
                wf = _inject(prompt, img_name, width=w, height=h, seed=seed,
                             prefix=f"hibtv_{seed}", text_only=False)
                prompt_id = await client.submit(advimage._prune(wf, advimage.NODE_SAVE))  # noqa: SLF001
                info = await client.wait_image_result(prompt_id)
                text_only = False
            else:
                raise

        content = await client.fetch_file(info["filename"], info.get("subfolder", ""),
                                          info.get("type", "output"))
        out_path.write_bytes(content)
        gap = _gap_sec()
        if gap > 0:
            await asyncio.sleep(gap)
    return {"path": str(out_path), "degraded": not text_only and not ref_path, "seed": seed}


async def generate_batch(jobs: Sequence[Dict]) -> List[Dict]:
    """按顺序出多张图（串行，节点侧本身就是串行队列）。"""
    results: List[Dict] = []
    for job in jobs:
        try:
            results.append(await generate_image(**job))
        except Exception as exc:  # noqa: BLE001 —— 单张失败不拖垮整批
            logger.warning("短剧出图失败（%s）：%s", job.get("out_path"), exc)
            results.append({"path": "", "error": str(exc)})
    return results


def describe() -> str:
    return json.dumps(status(), ensure_ascii=False)

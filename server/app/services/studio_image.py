"""剧集工坊出图：复用平台的 qwen21 节点与上传/落盘链路。

工作流模板 `workflows/studio_image_api.json` 由 shuohao 的 `qwenWorkflow` 直接导出
（`skills/character-refs/scripts/models.mjs`），因此模型、步数、cfg、shift、尺寸与上游逐项一致：

- 锚点（无参考图）→ `CLIPTextEncode` 文生图；
- 派生（有参考图）→ `TextEncodeQwenImageEditPlus`，参考图接 image1..3（最多 3 张）。

与广告图的区别：那一套是 PE i2i + 商品保真，这一套是纯提示词 + 参考图条件；
两边共用节点与上传接口，但工作流、提示词与计费各自独立。
"""
import asyncio
import hashlib
import json
import logging
import random
from pathlib import Path
from typing import Optional, Sequence

from ..config import STUDIO_DIR, WORKFLOW_DIR, settings
from . import comfyui

logger = logging.getLogger(__name__)

TEMPLATE_PATH = WORKFLOW_DIR / "studio_image_api.json"
_LOCK: Optional[asyncio.Lock] = None

# 与 shuohao 的 QWEN_SIZES 一致：16 的倍数、约 1.6–1.8MP、比例严格。
# 9:16 是平台追加的关键帧尺寸——关键帧要跟成片画幅一致，上游没有竖屏档。
QWEN_SIZES = {
    "2:3": (1056, 1584),
    "4:5": (1152, 1440),
    "1:1": (1328, 1328),
    "9:16": (960, 1696),
}

# 视图 → 画幅。front/side/back 是全身像 2:3，face-front 是半身 4:5，细节图 1:1。
VIEW_RATIO = {
    "front-full": "2:3",
    "side-full": "2:3",
    "back-full": "2:3",
    "face-front": "4:5",
}


def ratio_of(view: str) -> str:
    if view in VIEW_RATIO:
        return VIEW_RATIO[view]
    if (view or "").startswith("detail"):
        return "1:1"
    return "2:3"


def size_of(view: str) -> tuple:
    return QWEN_SIZES[ratio_of(view)]


def worker_url() -> str:
    return (settings.studio_image_worker or settings.advideo_image_worker or "").strip()


def available() -> bool:
    """节点与模板都在才算可用；缺一个就只报错不空转。"""
    return bool(worker_url()) and TEMPLATE_PATH.exists()


def status() -> dict:
    return {
        "enabled": bool(settings.studio_enabled),
        "worker": worker_url(),
        "template": str(TEMPLATE_PATH),
        "template_exists": TEMPLATE_PATH.exists(),
        "available": available(),
        "sizes": {k: list(v) for k, v in QWEN_SIZES.items()},
    }


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def load_template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def build_workflow(
    *,
    prompt: str,
    negative: str = "",
    ratio: str = "2:3",
    seed: int = 0,
    ref_names: Optional[Sequence[str]] = None,
    prefix: str = "studio/out",
) -> dict:
    """注入一次出图。ref_names 为空即文生图，否则换成 EditPlus 挂参考图。"""
    if ratio not in QWEN_SIZES:
        ratio = "2:3"
    width, height = QWEN_SIZES[ratio]
    wf = load_template()

    wf["lat"]["inputs"].update({"width": int(width), "height": int(height)})
    wf["ks"]["inputs"]["seed"] = int(seed)
    wf["neg"]["inputs"]["text"] = negative or ""
    wf["save"]["inputs"]["filename_prefix"] = prefix

    names = [str(n) for n in (ref_names or []) if n][:3]
    if not names:
        wf["pos"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["clip", 0], "text": prompt},
        }
    else:
        enc = {"clip": ["clip", 0], "vae": ["vae", 0], "prompt": prompt}
        for i, name in enumerate(names):
            nid = f"ref{i + 1}"
            wf[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
            enc[f"image{i + 1}"] = [nid, 0]
        wf["pos"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": enc}
    return wf


def output_path(project_id: int, ref_id: str, view: str, version: int = 1) -> Path:
    return STUDIO_DIR / "projects" / str(project_id) / "assets" / ref_id / f"{view}_v{int(version)}.png"


async def generate_image(
    *,
    prompt: str,
    negative: str = "",
    ratio: str = "2:3",
    seed: Optional[int] = None,
    ref_paths: Optional[Sequence[Path]] = None,
    prefix: str = "studio/out",
    out_path: Optional[Path] = None,
) -> dict:
    """生成一张图并落盘。返回 {path, sha256, prompt_id, worker}。"""
    if not available():
        raise RuntimeError(
            "剧集出图未配置：STUDIO_IMAGE_WORKER / ADVIDEO_IMAGE_WORKER 为空，"
            f"或模板 {TEMPLATE_PATH} 缺失"
        )

    client = comfyui.ComfyUIClient(worker_url())
    async with _lock():
        names = []
        for p in ref_paths or []:
            names.append(await client.upload_image(Path(p)))
        wf = build_workflow(
            prompt=prompt,
            negative=negative,
            ratio=ratio,
            seed=random.randint(0, 2 ** 63 - 1) if seed is None else int(seed),
            ref_names=names,
            prefix=prefix,
        )
        prompt_id = await client.submit(wf)
        info = await client.wait_image_result(prompt_id)
        content = await client.fetch_file(
            info["filename"], info.get("subfolder", ""), info.get("type", "output")
        )

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)
        target = str(out_path)
    else:
        target = ""

    return {
        "path": target,
        "sha256": hashlib.sha256(content).hexdigest(),
        "prompt_id": prompt_id,
        "worker": worker_url(),
        "bytes": len(content),
    }


async def generate_to_dir(
    *,
    project_id: int,
    ref_id: str,
    view: str,
    version: int = 1,
    prompt: str,
    negative: str = "",
    seed: Optional[int] = None,
    ref_paths: Optional[Sequence[Path]] = None,
) -> dict:
    """按项目的目录规范落盘：projects/<id>/assets/<C01>/<view>_v1.png。"""
    out = output_path(project_id, ref_id, view, version)
    return await generate_image(
        prompt=prompt,
        negative=negative,
        ratio=ratio_of(view),
        seed=seed,
        ref_paths=ref_paths,
        prefix=f"studio/p{project_id}/{ref_id}_{view}_v{version}",
        out_path=out,
    )

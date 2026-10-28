"""资产单图直出：一个角色 / 场景 / 道具只出 **1 张图**。

口径（改之前先读，别又把多视图长回来）：
- 正向词 = skill 产物里的 `image.prompt`（`HIBTV_IMAGE_PROMPT_LANG=zh` 时用 `promptLocal`）；
- 反向词 = `image.negativePrompt`；
- 尺寸按类型固定：cast 896×1152 / scene 1280×720 / prop 1024×1024（`HIBTV_ASSET_SIZE_*` 可覆盖）；
- 一次一张，落 `assets/{kind}/{code}/main.png` 并登记 Upload；
- **没有 sheet、没有裁格、没有 4 视图、没有「锚点锁 → 派生 → 过期」**。
  要换就重出这一张（改了提示词就按新提示词出），或者直接上传真图。
"""
import json
import logging
from pathlib import Path
from typing import Dict, Tuple

from sqlalchemy.orm import Session

from ..config import settings
from ..models import HibtvAsset, HibtvProject
from . import hibtv_image

logger = logging.getLogger(__name__)

# 尺寸兜底（配置解析失败时用，单位像素）
SIZE_FALLBACK: Dict[str, Tuple[int, int]] = {
    "cast": (896, 1152),
    "scene": (1280, 720),
    "prop": (1024, 1024),
}


def size_for(kind: str) -> Tuple[int, int]:
    sizes = getattr(settings, "hibtv_asset_sizes", {}) or {}
    return tuple(sizes.get(kind) or SIZE_FALLBACK.get(kind) or SIZE_FALLBACK["prop"])


def prompt_lang() -> str:
    return str(getattr(settings, "hibtv_image_prompt_lang", "en") or "en").strip().lower()


def prompt_of(data: Dict, lang: str = "") -> str:
    """`image.prompt` 原文（中文开关时取 `promptLocal`），只追加用户手写的改图要求。"""
    data = data or {}
    image = data.get("image") or {}
    use_zh = (lang or prompt_lang()).startswith("zh")
    if use_zh:
        text = image.get("promptLocal") or image.get("prompt") or data.get("prompt") or ""
    else:
        text = image.get("prompt") or image.get("promptLocal") or data.get("prompt") or ""
    base = str(text).strip()
    hint = str((data.get("hints") or {}).get("__main") or "").strip()
    if hint and hint not in base:
        return f"{base}；{hint[:400]}"
    return base


def negative_of(data: Dict) -> str:
    image = (data or {}).get("image") or {}
    return str(image.get("negativePrompt") or "").strip()


def _loads(raw) -> Dict:
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


async def draw(db: Session, project: HibtvProject, asset: HibtvAsset) -> Dict:
    """出一张主图（重出时把上一版当参考图，只换细节不换人）。"""
    from .hibtv_jobs import (abs_path, asset_status, jdumps, now, project_dir,
                             register_upload)

    data = _loads(asset.data)
    prompt = prompt_of(data)
    if not prompt:
        raise RuntimeError(f"{asset.code} 没有出图提示词（skill 没给 image.prompt）")
    width, height = size_for(asset.kind)
    rel = f"assets/{asset.kind}/{asset.code or asset.id}/main.png"
    out_abs = project_dir(project.id) / rel
    ref_abs = None
    if (asset.main_path or "").strip():
        cand = abs_path(project.id, asset.main_path)
        if cand.exists() and cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            ref_abs = cand
    result = await hibtv_image.generate_image(
        prompt=prompt, out_path=out_abs, ref_path=ref_abs, width=width, height=height,
        negative=negative_of(data), keep_adv_negative=False)
    if not out_abs.exists():
        raise RuntimeError(f"{asset.code} 出图没有落盘")

    version = int(asset.version or 0) + 1
    asset.main_path = rel
    asset.main_upload_id = register_upload(db, project.user_id, out_abs) or 0
    asset.main_meta = jdumps({
        "size": [width, height],
        "prompt": prompt,
        "negative": negative_of(data),
        "seed": result.get("seed") or 0,
        "mock": bool(result.get("mock")),
        "at": now().strftime("%Y-%m-%d %H:%M"),
    })
    asset.version = version
    asset.status = asset_status(asset)
    asset.updated_at = now()
    db.flush()
    logger.info("短剧资产图已出：%s %s（%dx%d v%d）",
                asset.kind, asset.code, width, height, version)
    return {"path": rel, "upload_id": int(asset.main_upload_id or 0), "version": version}


def import_image(db: Session, project: HibtvProject, asset: HibtvAsset,
                 path: Path, rel: str) -> Dict:
    """手工上传一张图顶掉主图（外部出图 / 人工修图）。"""
    from .hibtv_jobs import (asset_status, jdumps, now, register_upload)

    width, height = size_for(asset.kind)
    version = int(asset.version or 0) + 1
    asset.main_path = rel
    asset.main_upload_id = register_upload(db, project.user_id, path) or 0
    asset.main_meta = jdumps({
        "size": [width, height], "uploaded": True,
        "at": now().strftime("%Y-%m-%d %H:%M"),
    })
    asset.version = version
    asset.status = asset_status(asset)
    asset.updated_at = now()
    db.flush()
    return {"path": rel, "upload_id": int(asset.main_upload_id or 0), "version": version}


def clear_image(db: Session, project: HibtvProject, asset: HibtvAsset) -> None:
    """「隐藏不用」之外的彻底清空（重跑 skill 前重置用）。"""
    from .hibtv_jobs import asset_status, now

    asset.main_path = ""
    asset.main_upload_id = 0
    asset.main_meta = ""
    asset.status = asset_status(asset)
    asset.updated_at = now()
    db.flush()

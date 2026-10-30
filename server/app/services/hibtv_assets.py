"""资产多视图直出：**锚点主图 + N 张派生视图**（按原型 VIEW_DEFS 恢复）。

口径（skills 官方规则，改之前先读）：
- 锚点 = 每类第 1 个视图，走文生图，正向词 = skill 产物 `image.prompt`
  （`HIBTV_IMAGE_PROMPT_LANG=zh` 时用 `promptLocal`），反向词 = `image.negativePrompt`；
  落 `assets/{kind}/{code}/main.png`，存 `main_path`；
- 派生视图 = 挂锚点（cast 额外挂大头照 face-front）+ 一句改图指令，落
  `assets/{kind}/{code}/{view_id}.png`，存 `views` 列；比例/参考/过期照 character-refs schema.md；
- **锚点门**：锚点未确认（`data.imageConfirmed`）前不出派生视图；锚点重出 → 全派生标过期；
- 多视图只服务「人审阅」与「出分镜图时当参考」，**绝不进视频模型**（视频只吃干净单帧）。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import settings
from ..models import HibtvAsset, HibtvProject
from . import hibtv_image

logger = logging.getLogger(__name__)

# 尺寸兜底（配置解析失败时用，单位像素）——按锚点类型
SIZE_FALLBACK: Dict[str, Tuple[int, int]] = {
    "cast": (896, 1152),
    "scene": (1280, 720),
    "prop": (1024, 1024),
}

# 比例 → 像素（派生视图按 character-refs schema.md 的比例出图）
RATIO_SIZES: Dict[str, Tuple[int, int]] = {
    "2:3": (832, 1248),
    "4:5": (1024, 1280),
    "16:9": (1280, 720),
    "1:1": (1024, 1024),
}

# 每类视图定义（与原型 VIEW_DEFS 一致；第 1 个是锚点，ref = 出这张要挂的视图）
# cast 视图 id 对齐 character-refs：front-full / face-front / side-full / back-full
VIEW_DEFS: Dict[str, List[Dict]] = {
    "cast": [
        {"id": "front-full", "label": "正面全身", "ratio": "2:3", "anchor": True, "ref": []},
        {"id": "face-front", "label": "大头", "ratio": "4:5", "anchor": False, "ref": ["front-full"]},
        {"id": "side-full", "label": "侧面", "ratio": "2:3", "anchor": False, "ref": ["front-full"]},
        {"id": "back-full", "label": "背面", "ratio": "2:3", "anchor": False, "ref": ["front-full"]},
    ],
    "scene": [
        {"id": "front", "label": "正面", "ratio": "16:9", "anchor": True, "ref": []},
        {"id": "top", "label": "俯视", "ratio": "16:9", "anchor": False, "ref": ["front"]},
        {"id": "back", "label": "背面", "ratio": "16:9", "anchor": False, "ref": ["front"]},
        {"id": "left", "label": "左侧", "ratio": "16:9", "anchor": False, "ref": ["front"]},
        {"id": "right", "label": "右侧", "ratio": "16:9", "anchor": False, "ref": ["front"]},
    ],
    "prop": [
        {"id": "front", "label": "正面", "ratio": "1:1", "anchor": True, "ref": []},
        {"id": "back", "label": "背面", "ratio": "1:1", "anchor": False, "ref": ["front"]},
        {"id": "left", "label": "左侧", "ratio": "1:1", "anchor": False, "ref": ["front"]},
        {"id": "right", "label": "右侧", "ratio": "1:1", "anchor": False, "ref": ["front"]},
        {"id": "top", "label": "俯视", "ratio": "1:1", "anchor": False, "ref": ["front"]},
    ],
}

# 每类的锚点视图 id
ANCHOR_OF: Dict[str, str] = {"cast": "front-full", "scene": "front", "prop": "front"}

# 派生视图的反向词：禁多主体 / 拼格 / 屏内文字（多视图网格只在这一层出，绝不进视频）
DERIVE_NEGATIVE = (
    "text, subtitle, watermark, logo, signature, caption, border, frame, "
    "multiple panels, split screen, contact sheet, collage, multi-view grid, "
    "duplicate person, cloned face, identical twins, extra person, extra hands, "
    "character sheet")


def view_defs(kind: str) -> List[Dict]:
    return VIEW_DEFS.get(kind) or VIEW_DEFS["prop"]


def view_spec(kind: str, view_id: str) -> Optional[Dict]:
    for spec in view_defs(kind):
        if spec["id"] == view_id:
            return spec
    return None


def anchor_id(kind: str) -> str:
    return ANCHOR_OF.get(kind, "front")


def size_for(kind: str, view_id: str = "") -> Tuple[int, int]:
    """锚点沿用按类型的配置尺寸；派生视图按视图比例出图。"""
    spec = view_spec(kind, view_id) if view_id else None
    if spec is not None and not spec.get("anchor"):
        return RATIO_SIZES.get(spec["ratio"], SIZE_FALLBACK.get(kind, (1024, 1024)))
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
    # 锚点（重）出 → 新版本需重新确认；未确认前派生视图锁定（锚点门）
    data["imageConfirmed"] = False
    asset.data = jdumps(data)
    asset.status = asset_status(asset)
    asset.updated_at = now()
    # 锚点重出 → 全部派生视图过期（只标不删）
    mark_stale(asset, "__anchor")
    db.flush()
    logger.info("短剧资产图已出：%s %s（%dx%d v%d）",
                asset.kind, asset.code, width, height, version)
    return {"path": rel, "upload_id": int(asset.main_upload_id or 0), "version": version}


def derive_prompt(kind: str, view_id: str, data: Dict) -> str:
    """派生视图的改图指令：锁定锚点身份 + 一句视角指令（英文，出图引擎吃英文最稳）。

    cast 对齐 character-refs（同脸/同发/同装/同身份）；scene/prop 套 novel-art variant
    （keep structure/materials/wear identical）。
    """
    spec = view_spec(kind, view_id) or {}
    label = str(spec.get("label") or view_id)
    if kind == "cast":
        lock = ("Keep the EXACT same person as the reference: identical face, facial features, "
                "hairstyle, body build, outfit, accessories and identity. Do not change who it is.")
        angle = {
            "face-front": ("Close-up head-and-shoulders portrait, front view, neutral expression, "
                           "plain uncluttered background, face fills the frame."),
            "side-full": ("Full-body side profile view (90 degrees), same outfit, standing pose, "
                          "plain background."),
            "back-full": ("Full-body back view from behind, showing hairstyle and outfit, "
                          "no face visible, plain background."),
        }.get(view_id, f"{label} view, same outfit, plain background.")
        tail = "Single clean full-bleed image. No text, no watermark, no borders."
        return f"{lock} {angle} {tail}"
    if kind == "scene":
        angle = {
            "top": "top-down overhead view",
            "back": "view from the back / opposite side",
            "left": "view from the left side",
            "right": "view from the right side",
        }.get(view_id, f"{label} view")
        return ("Same environment as the reference: keep structure, architecture, materials, "
                f"lighting, color palette and wear identical. Now show the {angle}. "
                "No people. Single clean full-bleed image. No text, no watermark, no borders.")
    angle = {
        "back": "back side",
        "left": "left side",
        "right": "right side",
        "top": "top-down",
    }.get(view_id, f"{label}")
    return ("Same prop as the reference: keep shape, materials, colors, proportions and wear "
            f"identical. Now show the {angle} view. Isolated on a pure white background, "
            "no people, no hands. Single clean image. No text, no watermark, no borders.")


async def draw_view(db: Session, project: HibtvProject, asset: HibtvAsset,
                    view_id: str) -> Dict:
    """出一张派生视图：挂锚点(+大头照) 参考 + 一句改图指令，写入 `views[view_id]`。"""
    from .hibtv_jobs import (abs_path, jdumps, now, project_dir, register_upload)

    spec = view_spec(asset.kind, view_id)
    if spec is None:
        raise RuntimeError(f"{asset.code} 没有视图 {view_id}")
    if spec.get("anchor"):
        raise RuntimeError(f"{view_id} 是锚点，请用 draw() 出图")
    data = _loads(asset.data)
    if not data.get("imageConfirmed"):
        raise RuntimeError(f"{asset.code} 先确认锚点，再出派生视图")

    views = _loads(asset.views)
    ref_paths: List[Path] = []
    for dep in [anchor_id(asset.kind)] + list(spec.get("ref") or []):
        if dep == anchor_id(asset.kind):
            rel = (asset.main_path or "").strip()
        else:
            rel = str((views.get(dep) or {}).get("path") or "").strip()
        if not rel:
            continue
        cand = abs_path(project.id, rel)
        if cand.exists() and cand not in ref_paths:
            ref_paths.append(cand)
    if not ref_paths:
        raise RuntimeError(f"{asset.code} 锚点还没出图，不能派生 {view_id}")

    prompt = derive_prompt(asset.kind, view_id, data)
    width, height = size_for(asset.kind, view_id)
    rel = f"assets/{asset.kind}/{asset.code or asset.id}/{view_id}.png"
    out_abs = project_dir(project.id) / rel
    result = await hibtv_image.generate_image(
        prompt=prompt, out_path=out_abs, ref_paths=ref_paths, width=width, height=height,
        negative=DERIVE_NEGATIVE, keep_adv_negative=True)
    if not out_abs.exists():
        raise RuntimeError(f"{asset.code} 派生视图 {view_id} 出图没有落盘")

    prev = views.get(view_id) or {}
    version = int(prev.get("version") or 0) + 1
    upload_id = register_upload(db, project.user_id, out_abs) or 0
    views[view_id] = {
        "path": rel, "uploadId": int(upload_id), "version": version,
        "status": "review", "stale": False, "confirmed": bool(prev.get("confirmed")),
        "prompt": prompt,
        "refs": [{"view": p.name} for p in ref_paths],
        "size": [width, height], "seed": result.get("seed") or 0,
        "mock": bool(result.get("mock")),
        "at": now().strftime("%Y-%m-%d %H:%M"),
    }
    asset.views = jdumps(views)
    asset.updated_at = now()
    # 大头照重出 → 挂了大头照的派生视图过期
    mark_stale(asset, view_id)
    db.flush()
    logger.info("短剧派生视图已出：%s %s %s（%dx%d v%d）",
                asset.kind, asset.code, view_id, width, height, version)
    return {"path": rel, "upload_id": int(upload_id), "version": version}


def mark_stale(asset: HibtvAsset, changed: str) -> None:
    """按 character-refs schema.md 过期规则标过期（只标不删）。

    - `changed == "__anchor"` 或锚点视图 id：全部派生视图过期；
    - 某派生视图重出：只让把它当参考的视图过期。
    """
    from .hibtv_jobs import jdumps

    views = _loads(asset.views)
    if not views:
        return
    aid = anchor_id(asset.kind)
    anchor_changed = changed in ("__anchor", aid)
    dirty = False
    for vid, item in views.items():
        if not isinstance(item, dict):
            continue
        spec = view_spec(asset.kind, vid)
        deps = list((spec or {}).get("ref") or [])
        if anchor_changed or changed in deps:
            if not item.get("stale"):
                item["stale"] = True
                dirty = True
    if dirty:
        asset.views = jdumps(views)


def import_image(db: Session, project: HibtvProject, asset: HibtvAsset,
                 path: Path, rel: str, view: str = "") -> Dict:
    """手工上传一张图顶掉锚点主图（view 为空）或指定派生视图。"""
    from .hibtv_jobs import (asset_status, jdumps, now, register_upload)

    view = (view or "").strip()
    if view and not view_spec(asset.kind, view):
        raise RuntimeError(f"{asset.code} 没有视图 {view}")
    upload_id = register_upload(db, project.user_id, path) or 0
    if view and view != anchor_id(asset.kind):
        views = _loads(asset.views)
        prev = views.get(view) or {}
        version = int(prev.get("version") or 0) + 1
        width, height = size_for(asset.kind, view)
        views[view] = {
            "path": rel, "uploadId": int(upload_id), "version": version,
            "status": "review", "stale": False, "confirmed": bool(prev.get("confirmed")),
            "uploaded": True, "size": [width, height],
            "at": now().strftime("%Y-%m-%d %H:%M"),
        }
        asset.views = jdumps(views)
        asset.updated_at = now()
        mark_stale(asset, view)
        db.flush()
        return {"path": rel, "upload_id": int(upload_id), "version": version}

    width, height = size_for(asset.kind)
    version = int(asset.version or 0) + 1
    asset.main_path = rel
    asset.main_upload_id = upload_id
    asset.main_meta = jdumps({
        "size": [width, height], "uploaded": True,
        "at": now().strftime("%Y-%m-%d %H:%M"),
    })
    asset.version = version
    # 上传新锚点 → 同样需重新确认；未确认前派生视图锁定
    data = _loads(asset.data)
    data["imageConfirmed"] = False
    asset.data = jdumps(data)
    asset.status = asset_status(asset)
    asset.updated_at = now()
    # 上传新锚点同样让全部派生视图过期
    mark_stale(asset, "__anchor")
    db.flush()
    return {"path": rel, "upload_id": int(asset.main_upload_id or 0), "version": version}


def clear_image(db: Session, project: HibtvProject, asset: HibtvAsset) -> None:
    """「隐藏不用」之外的彻底清空（重跑 skill 前重置用）。"""
    from .hibtv_jobs import asset_status, jdumps, now

    asset.main_path = ""
    asset.main_upload_id = 0
    asset.main_meta = ""
    asset.views = jdumps({})
    asset.status = asset_status(asset)
    asset.updated_at = now()
    db.flush()

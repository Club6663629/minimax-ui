"""关键帧出图：**每切一张，f1..fN**（ref2v 唯一的图源）。

口径：
- 提示词 = `cut.frame`（分镜给的关键帧描述）+ 该场 `lighting` + 一句竖屏构图/禁文字水印；
- 参考图 = 该场场景主图 + 该切出场角色主图（最多 3 张，模型自己补看不到的角度）；
- 画布按**输出网格**出：9:16 → 768×1344，16:9 → 1344×768（`comfyui.SOURCE_SIZES`）；
- 落 `frames/{seg}/f{n}.png` 并登记 Upload（出片时按 `picture` 序号挂给 H3）；
- 缺一张就拦出片：ref2v 少一张图，切点之后会整段切到空白。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import HibtvAsset, HibtvFrame, HibtvProject, HibtvSegment
from . import comfyui, hibtv_image

logger = logging.getLogger(__name__)

# 每切最多挂几张参考图：场景 1 + 角色 2（再多会把画面挤成拼贴，模型也不认）
MAX_REFS = 3
# 画面硬约束：竖屏构图 + 禁屏内文字（平台唯一允许追加的两句，属像素约束不是内容）
FRAME_TAIL = "竖屏构图，画面里不出现字幕、水印或任何叠加文字"
NEGATIVE = ("text, subtitle, watermark, logo, signature, caption, border, frame, "
            "multiple panels, split screen, contact sheet, collage")


def _loads(raw) -> Dict:
    try:
        val = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return val if isinstance(val, dict) else {}


def canvas(project: HibtvProject) -> Tuple[int, int]:
    """关键帧必须落在 9:16 / 16:9 原生画布网格上（与 H3 的出图分辨率一致）。"""
    ratio = "16:9" if (project.aspect_ratio or "").startswith("16") else "9:16"
    return comfyui.SOURCE_SIZES.get(ratio, comfyui.SOURCE_SIZES["9:16"])


def rel_for(frame: HibtvFrame) -> str:
    return f"frames/{frame.seg_id}/f{int(frame.cut_index)}.png"


def lighting_of(db: Session, project: HibtvProject, ep: int, scene_index: int) -> str:
    """从剧本产物里取这一场的灯光描述（分镜的 `sceneIndex` 是 1 起）。"""
    from .hibtv_jobs import get_doc, jloads

    doc = get_doc(db, project.id, "script", ep)
    if doc is None:
        return ""
    for row in (jloads(doc.data, {}).get("episodes") or []):
        if int(row.get("ep") or 0) != int(ep):
            continue
        scenes = row.get("scenes") or []
        if 0 < int(scene_index or 0) <= len(scenes):
            return str(scenes[int(scene_index) - 1].get("lighting") or "").strip()
    return ""


def prompt_for(frame: HibtvFrame, lighting: str = "") -> str:
    base = (frame.prompt or "").strip()
    parts = [p for p in (base, lighting.strip(), FRAME_TAIL) if p]
    return "；".join(parts) if parts else FRAME_TAIL


def _asset_main(db: Session, project: HibtvProject, kind: str, code: str) -> Optional[Path]:
    from .hibtv_jobs import abs_path

    if not code:
        return None
    asset = (db.query(HibtvAsset)
             .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == kind,
                     HibtvAsset.code == code)
             .first())
    if asset is None or not (asset.main_path or "").strip():
        return None
    path = abs_path(project.id, asset.main_path)
    return path if path.exists() else None


def _scene_code(db: Session, project: HibtvProject, ep: int, seg: Optional[HibtvSegment]) -> str:
    """段 → 场 → 场景资产 code（分镜的 `sceneIndex` 与剧本 `scenes[]` 同序）。"""
    from .hibtv_jobs import get_doc, jloads

    if seg is None:
        return ""
    idx = int(_loads(seg.data).get("sceneIndex") or 0)
    doc = get_doc(db, project.id, "script", ep)
    if doc is not None and idx > 0:
        for row in (jloads(doc.data, {}).get("episodes") or []):
            if int(row.get("ep") or 0) != int(ep):
                continue
            scenes = row.get("scenes") or []
            if idx <= len(scenes):
                code = str(scenes[idx - 1].get("sceneId") or "").strip()
                if code:
                    return code
    return ""


def refs_for(db: Session, project: HibtvProject, frame: HibtvFrame,
             seg: Optional[HibtvSegment]) -> List[Path]:
    """该切的参考图：场景主图 + 出场角色主图（≤ MAX_REFS）。"""
    out: List[Path] = []
    scene = _asset_main(db, project, "scene", _scene_code(db, project, int(frame.ep), seg))
    if scene is not None:
        out.append(scene)
    for code in (_loads(frame.meta).get("characters") or [])[:MAX_REFS - len(out)]:
        path = _asset_main(db, project, "cast", str(code or ""))
        if path is not None and path not in out:
            out.append(path)
    return out[:MAX_REFS]


async def draw(db: Session, project: HibtvProject, frame: HibtvFrame) -> Dict:
    """出一张关键帧并落 `frames/{seg}/f{n}.png`。"""
    from .hibtv_jobs import abs_path, jdumps, now, project_dir, register_upload

    payload = _loads(frame.meta)
    seg = (db.query(HibtvSegment)
           .filter(HibtvSegment.project_id == project.id, HibtvSegment.seg_id == frame.seg_id)
           .first())
    lighting = payload.get("lighting") or lighting_of(db, project, int(frame.ep),
                                                     int((_loads(seg.data).get("sceneIndex")
                                                          if seg else 0) or 0))
    prompt = prompt_for(frame, str(lighting or ""))
    width, height = canvas(project)
    rel = rel_for(frame)
    out_abs = project_dir(project.id) / rel
    old_abs = abs_path(project.id, frame.path) if (frame.path or "").strip() else None
    ref_paths = [p for p in refs_for(db, project, frame, seg)
                 if p != (old_abs if old_abs and old_abs.exists() else None)]
    result = await hibtv_image.generate_image(
        prompt=prompt, out_path=out_abs, ref_paths=ref_paths, width=width, height=height,
        negative=NEGATIVE, keep_adv_negative=True, grid=True)
    if not out_abs.exists():
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 出图没有落盘")

    frame.path = rel
    frame.prompt = prompt
    frame.upload_id = register_upload(db, project.user_id, out_abs) or 0
    frame.refs = jdumps([str(p) for p in ref_paths])
    frame.version = int(frame.version or 0) + 1
    frame.status = "review"
    frame.updated_at = now()
    payload["size"] = [width, height]
    payload["seed"] = result.get("seed") or 0
    payload["at"] = now().strftime("%Y-%m-%d %H:%M")
    payload["cost"] = payload.get("cost") or 0
    frame.meta = jdumps(payload)
    db.flush()
    return {"path": rel, "upload_id": int(frame.upload_id or 0), "version": frame.version}


def plan(db: Session, project: HibtvProject, seg_id: str,
         ep: Optional[int] = None) -> List[Dict]:
    """段的参考图清单：按切号排好的帧（顺序即 Picture 序号）。"""
    query = db.query(HibtvFrame).filter(HibtvFrame.project_id == project.id,
                                        HibtvFrame.seg_id == seg_id)
    if ep:
        query = query.filter(HibtvFrame.ep == int(ep))
    rows = query.order_by(HibtvFrame.cut_index).all()
    out: List[Dict] = []
    for i, row in enumerate(rows, start=1):
        rel = (row.path or "").strip()
        out.append({
            "n": i, "cutIndex": int(row.cut_index), "frameId": row.id,
            "uploadId": int(row.upload_id or 0), "path": rel,
            "url": f"/files/hibtv/{project.id}/{rel}" if rel else "",
            "status": row.status or "empty",
        })
    return out


def missing(db: Session, project: HibtvProject, seg_id: str,
            ep: Optional[int] = None) -> List[int]:
    """缺图的切号清单（有切但没出图，或出了图没登记 Upload）。"""
    return [p["cutIndex"] for p in plan(db, project, seg_id, ep)
            if not p["path"] or not p["uploadId"]]


def ensure_ready(db: Session, project: HibtvProject, seg_id: str,
                 ep: Optional[int] = None) -> List[Dict]:
    """出片前的硬校验：帧必须齐、张数必须等于切数，否则抛错。"""
    rows = plan(db, project, seg_id, ep)
    if not rows:
        raise RuntimeError(f"{seg_id} 还没有关键帧清单，先跑分镜")
    lack = [p["cutIndex"] for p in rows if not p["path"] or not p["uploadId"]]
    if lack:
        shown = "、".join(f"f{i}" for i in lack[:8])
        raise RuntimeError(f"{seg_id} 缺 {len(lack)} 张关键帧（{shown}…），补齐才能出片")
    if len(rows) > 9:
        raise RuntimeError(f"{seg_id} 有 {len(rows)} 切，超过 H3 参考图上限 9 张，需要先拆段")
    return rows

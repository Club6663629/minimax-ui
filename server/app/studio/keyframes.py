"""关键帧：每切一张，按景别挑设定图当参考，画幅跟成片一致（9:16）。

一张关键帧 = 一次 qwen21 改图：
- 提示词取 storyboard 的 `cut.frame`（上游 `frame-prompt` 门保证中文非空）；
- 参考图按景别挑：远景/中景挂正面全身，近景/特写挂正脸（不够再补正面全身）；
- 角色优先、场景一张、总数 ≤3（EditPlus 的 image1..3 上限）；
- 角色没出设定图就报缺——关键帧不能凭空画人，但空场切允许纯文生图。

路径规范：`projects/<id>/ep01/keyframes/E01-01_c01.png`，重出覆盖同名文件。
"""
import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import STUDIO_DIR
from . import docs as docmod
from .models import StudioAsset, StudioAssetView

RATIO = "9:16"
MAX_REFS = 3

# 景别 → 角色参考视图（按优先级）。特写先挂正脸，模型才会把脸画大；
# 全身景别先挂正面全身，不然特写图会把镜头拽近。
SIZE_VIEWS = {
    "extreme-wide": ("front-full",),
    "wide": ("front-full",),
    "medium": ("front-full",),
    "close": ("face-front", "front-full"),
    "extreme-close": ("face-front", "front-full"),
}


def output_path(project_id: int, ep_no: int, segment_id: str, cut_index: int) -> Path:
    return (
        STUDIO_DIR
        / "projects"
        / str(int(project_id))
        / f"ep{int(ep_no):02d}"
        / "keyframes"
        / f"{segment_id}_c{int(cut_index):02d}.png"
    )


def _json(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _latest_view(db: Session, asset_id: int, view: str) -> Optional[StudioAssetView]:
    return (
        db.query(StudioAssetView)
        .filter(StudioAssetView.asset_id == asset_id, StudioAssetView.view == view)
        .order_by(StudioAssetView.version.desc())
        .first()
    )


def _char_index(db: Session, project_id: int) -> dict:
    """cast 的 id（C01）与 ref_id 都指向同一个资产。"""
    index: dict = {}
    rows = db.query(StudioAsset).filter(
        StudioAsset.project_id == project_id, StudioAsset.kind == "character"
    )
    for asset in rows:
        index[asset.ref_id] = asset
        cid = _json(asset.data).get("id")
        if cid:
            index[str(cid)] = asset
    return index


def _scene_of(db: Session, project_id: int, scene_id: str) -> Optional[StudioAsset]:
    return (
        db.query(StudioAsset)
        .filter(
            StudioAsset.project_id == project_id,
            StudioAsset.kind == "scene",
            StudioAsset.ref_id == scene_id,
        )
        .first()
    )


def _pick(db: Session, asset: StudioAsset, views: tuple) -> Optional[StudioAssetView]:
    for view in views:
        row = _latest_view(db, asset.id, view)
        if row is not None and Path(row.path).exists():
            return row
    return None


def refs_for_cut(db: Session, project_id: int, cut: dict, scene_id: str = "") -> tuple:
    """(参考图路径, 缺失项)。角色优先、场景一张、总数 ≤3。"""
    views = SIZE_VIEWS.get(str(cut.get("size") or "medium"), ("front-full",))
    chars = _char_index(db, project_id)
    paths: list = []
    missing: list = []

    for cid in (cut.get("characters") or [])[:MAX_REFS]:
        asset = chars.get(str(cid))
        if asset is None:
            missing.append(f"缺角色资产 {cid}")
            continue
        row = _pick(db, asset, views)
        if row is None:
            missing.append(f"{asset.name} 的设定图没出（{'/'.join(views)}）")
            continue
        paths.append(row.path)

    if scene_id and len(paths) < MAX_REFS:
        scene = _scene_of(db, project_id, scene_id)
        if scene is not None:
            row = _pick(db, scene, ("sheet",))
            if row is not None:
                paths.append(row.path)

    return paths[:MAX_REFS], missing


def prompt_of(cut: dict, look: str = "") -> str:
    """关键帧提示词 = 分镜图提示词（中文）+ 项目风格 + 竖屏构图。"""
    text = str(cut.get("frame") or "").strip()
    tail = []
    if look:
        tail.append(f"画面风格：{look}")
    tail.append(f"竖屏 {RATIO} 构图")
    if not text:
        return "。".join(tail) + "。"
    return f"{text.rstrip('。')}。{'。'.join(tail)}。"


def _episode_doc(docs: dict, ep_no: int) -> dict:
    episodes = docs.get("episodes") or []
    for item in episodes:
        if int(item.get("ep") or 0) == int(ep_no):
            return item
    return episodes[0] if episodes else {}


def segment_plan(db: Session, project, episode, segment_id: str) -> dict:
    """一段的逐切排单：切号、提示词、参考图、缺失项、落盘路径。"""
    board = docmod.get_doc(db, project.id, "storyboard", episode.id) or {}
    ep_doc = _episode_doc(board, episode.ep_no)
    seg = next(
        (s for s in (ep_doc.get("segments") or []) if s.get("id") == segment_id), None
    )
    if seg is None:
        raise ValueError(f"这一段不在切表里：{segment_id}")

    script = docmod.get_doc(db, project.id, "script", episode.id) or {}
    scenes = _episode_doc(script, episode.ep_no).get("scenes") or []
    scene_idx = int(seg.get("sceneIndex") or 0) - 1
    scene_id = str(scenes[scene_idx].get("sceneId") or "") if 0 <= scene_idx < len(scenes) else ""

    look = str(board.get("look") or "")
    cuts = []
    for i, cut in enumerate(seg.get("cuts") or [], start=1):
        refs, missing = refs_for_cut(db, project.id, cut, scene_id)
        cuts.append(
            {
                "cut_index": i,
                "prompt": prompt_of(cut, look),
                "refs": refs,
                "missing": missing,
                "path": str(output_path(project.id, episode.ep_no, segment_id, i)),
            }
        )
    return {"segment": seg, "sceneId": scene_id, "cuts": cuts}

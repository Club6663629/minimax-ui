"""HiBTV 镜头素材、可选分镜图与路线 A 段级视频装配。

路线 A 的执行单位是段：每个镜头保存自己的引用素材，生成视频时按时间顺序
汇总、去重并作为一次 ref2v 请求发送。关键帧 ``f1..fN`` 只作为可选的构图
控制，不再是视频生成的硬前置，也不参与默认 Picture 时间点映射。
"""
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy.orm import Session

from ..models import HibtvAsset, HibtvFrame, HibtvProject, HibtvSegment, Upload
from . import comfyui, hibtv_assets, hibtv_image
from .hibtv_contracts import COMPOSITION_FIELDS, dependencies_valid, digest, file_hash

logger = logging.getLogger(__name__)

MAX_IMAGE_REFS = 6
MAX_VIDEO_REFS = 9
FRAME_TAIL = "画面里不出现字幕、水印或任何叠加文字"
CLEAN_TAIL = "No text, no watermark, no borders — a single clean full-bleed frame."
NEGATIVE = (
    "contact sheet, collage, split screen, split frame, multi-panel, multiple panels, "
    "image grid, character sheet, multi-view grid, duplicate person, cloned face, "
    "identical twins, repeated subject, extra person, extra people, text, subtitle, "
    "watermark, logo, caption, border, frame"
)

ROLE_PRIORITY = {
    "continuity-frame": 0,
    "scene-top": 1,
    "scene": 2,
    "cast-anchor": 3,
    "cast-face": 4,
    "prop": 5,
}


def _loads(raw) -> Dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _loads_list(raw) -> List:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def canvas(project: HibtvProject) -> Tuple[int, int]:
    ratio = "16:9" if (project.aspect_ratio or "").startswith("16") else "9:16"
    return comfyui.SOURCE_SIZES.get(ratio, comfyui.SOURCE_SIZES["9:16"])


def frame_tail(project: HibtvProject) -> str:
    ratio = (project.aspect_ratio or "9:16").strip()
    orient = "横屏构图（16:9）" if ratio.startswith("16") else "竖屏构图（9:16）"
    return f"{orient}，{FRAME_TAIL}。{CLEAN_TAIL}"


def rel_for(frame: HibtvFrame) -> str:
    return f"frames/{frame.seg_id}/f{int(frame.cut_index)}-{uuid4().hex}.png"


def lighting_of(db: Session, project: HibtvProject, ep: int, scene_index: int) -> str:
    from .hibtv_jobs import get_doc, jloads

    doc = get_doc(db, project.id, "script", ep)
    if doc is None:
        return ""
    for row in jloads(doc.data, {}).get("episodes") or []:
        if int(row.get("ep") or 0) != int(ep):
            continue
        scenes = row.get("scenes") or []
        if 0 < int(scene_index or 0) <= len(scenes):
            return str(scenes[int(scene_index) - 1].get("lighting") or "").strip()
    return ""


def _asset_row(db: Session, project: HibtvProject, kind: str, code: str) -> Optional[HibtvAsset]:
    if not code:
        return None
    return (
        db.query(HibtvAsset)
        .filter(
            HibtvAsset.project_id == project.id,
            HibtvAsset.kind == kind,
            HibtvAsset.code == code,
        )
        .first()
    )


def _asset_main(db: Session, project: HibtvProject, kind: str, code: str) -> Optional[Path]:
    from .hibtv_jobs import abs_path

    asset = _asset_row(db, project, kind, code)
    if asset is None or not hibtv_assets.main_current(project, asset):
        return None
    path = abs_path(project.id, asset.main_path)
    return path if path.exists() else None


def _ref_from_asset(
    db: Session,
    project: HibtvProject,
    asset: Optional[HibtvAsset],
    role: str = "",
    *,
    view: str = "",
    note: str = "",
) -> Optional[Dict]:
    from .hibtv_jobs import abs_path

    if asset is None or not (asset.main_path or "").strip():
        return None
    path = abs_path(project.id, asset.main_path)
    if not path.exists():
        return None
    default_role = {"scene": "scene", "prop": "prop", "cast": "cast-anchor"}.get(
        asset.kind, asset.kind
    )
    return {
        "assetId": int(asset.id),
        "kind": asset.kind,
        "code": asset.code,
        "name": asset.name,
        "path": path,
        "role": role or default_role,
        "view": view or hibtv_assets.anchor_id(asset.kind),
        "note": note,
        "uploadId": int(asset.main_upload_id or 0),
    }


def _view_ref(
    db: Session,
    project: HibtvProject,
    asset: Optional[HibtvAsset],
    view_id: str,
    role: str,
    note: str = "",
) -> Optional[Dict]:
    """只取已确认且未过期的干净单视图。"""
    from .hibtv_jobs import abs_path

    if asset is None or not hibtv_assets.view_current(project, asset, view_id):
        return None
    item = (_loads(asset.views) or {}).get(view_id) or {}
    rel = str((item or {}).get("path") or "").strip()
    if not rel:
        return None
    path = abs_path(project.id, rel)
    if not path.is_file():
        return None
    return {
        "assetId": int(asset.id),
        "kind": asset.kind,
        "code": asset.code,
        "name": asset.name,
        "path": path,
        "role": role,
        "view": view_id,
        "note": note,
        "uploadId": int((item or {}).get("uploadId") or 0),
    }


def _scene_code(
    db: Session,
    project: HibtvProject,
    ep: int,
    seg: Optional[HibtvSegment],
) -> str:
    from .hibtv_jobs import get_doc, jloads

    if seg is None:
        return ""
    idx = int(_loads(seg.data).get("sceneIndex") or 0)
    doc = get_doc(db, project.id, "script", ep)
    if doc is None or idx <= 0:
        return ""
    for row in jloads(doc.data, {}).get("episodes") or []:
        if int(row.get("ep") or 0) != int(ep):
            continue
        scenes = row.get("scenes") or []
        if idx <= len(scenes):
            return str(scenes[idx - 1].get("sceneId") or "").strip()
    return ""


def _cast_assets(db: Session, project: HibtvProject) -> List[HibtvAsset]:
    return (
        db.query(HibtvAsset)
        .filter(
            HibtvAsset.project_id == project.id,
            HibtvAsset.kind == "cast",
            HibtvAsset.hidden.is_(False),
        )
        .order_by(HibtvAsset.sort, HibtvAsset.id)
        .all()
    )


def visible_character_codes(db: Session, project: HibtvProject, frame: HibtvFrame) -> List[str]:
    """合并结构字段与画面文字中明确出现的角色。

    技能偶尔会写出 ``frame`` 里有人、``characters`` 为空的分镜。只相信数组会让
    生图模型在没有身份参考的情况下自行发明人物，因此这里按资产 code/name 做补全。
    只扫描本镜 frame 与 shot，不扫描段级 blocking，避免把画外人物加进画面。
    """
    meta = _loads(frame.meta)
    cut = meta.get("cut") or meta
    raw_codes = [str(item).strip() for item in (meta.get("characters") or [])]
    text = " ".join(
        [
            str(cut.get("frame") or frame.prompt or ""),
            str(cut.get("shot") or ""),
        ]
    )
    out: List[str] = []
    for asset in _cast_assets(db, project):
        code = str(asset.code or "")
        name = str(asset.name or "")
        if not code:
            continue
        if code in raw_codes or (name and name in text) or (code and code in text):
            if code not in out:
                out.append(code)
    for code in raw_codes:
        if code and code not in out:
            out.append(code)
    return out


def character_specs_for(db: Session, project: HibtvProject, frame: HibtvFrame) -> List[Dict]:
    out: List[Dict] = []
    for code in visible_character_codes(db, project, frame):
        asset = _asset_row(db, project, "cast", code)
        data = _loads(asset.data) if asset is not None else {}
        persona = data.get("persona") or {}
        out.append(
            {
                "code": code,
                "name": str(data.get("name") or (asset.name if asset else code)),
                "gender": str(persona.get("gender") or ""),
                "identity": str(persona.get("identity") or ""),
            }
        )
    return out


def _state_view(asset: HibtvAsset, state: str) -> Optional[str]:
    if not state:
        return None
    spec = next(
        (
            item
            for item in hibtv_assets.view_defs(asset.kind, _loads(asset.data))
            if item.get("state") == state
        ),
        None,
    )
    return spec["id"] if spec else None


def _default_role(asset: HibtvAsset, view: str) -> str:
    if asset.kind == "scene":
        return "scene-top" if view == "top" else "scene"
    if asset.kind == "cast":
        return "cast-face" if view == "face-front" else "cast-anchor"
    return asset.kind


def resolve_reference_item(
    db: Session,
    project: HibtvProject,
    item: Dict,
) -> Dict:
    """把前端选择的 ``{assetId, view, role, note}`` 解析成当前图片。"""
    asset = db.get(HibtvAsset, int(item.get("assetId") or 0))
    if asset is None or asset.project_id != project.id:
        raise RuntimeError("引用素材不存在")
    view = str(item.get("view") or "").strip()
    note = str(item.get("note") or "").strip()[:200]
    anchor = hibtv_assets.anchor_id(asset.kind)
    role = _default_role(asset, view or anchor)
    if not view or view == anchor:
        ref = _ref_from_asset(db, project, asset, role or _default_role(asset, anchor), note=note)
    else:
        ref = _view_ref(
            db,
            project,
            asset,
            view,
            role or _default_role(asset, view),
            note,
        )
    if not ref:
        raise RuntimeError(f"{asset.code} 的 {view or '主图'} 缺失、未确认或已过期")
    return ref


def saved_reference_specs(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
) -> List[Dict]:
    specs: List[Dict] = []
    for item in _loads_list(frame.refs):
        if not isinstance(item, dict) or not item.get("assetId"):
            continue
        specs.append(resolve_reference_item(db, project, item))
    return specs


def _auto_reference_specs(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
    seg: Optional[HibtvSegment],
    *,
    include_top: bool,
) -> List[Dict]:
    meta = _loads(frame.meta)
    cut = meta.get("cut") or meta
    code = _scene_code(db, project, int(frame.ep), seg)
    scene = _asset_row(db, project, "scene", code)
    if scene is None:
        raise RuntimeError(f"{frame.seg_id} f{frame.cut_index} 找不到场景资产 {code}")

    specs: List[Dict] = []
    explicit_state = str(cut.get("sceneState") or "").strip()
    scene_view = _state_view(scene, explicit_state) if explicit_state else ""
    if explicit_state and not scene_view:
        raise RuntimeError(f"{scene.code} 没有登记状态 {explicit_state}")
    scene_ref = (
        _view_ref(db, project, scene, scene_view, "scene")
        if scene_view
        else _ref_from_asset(db, project, scene, "scene")
    )
    if not scene_ref:
        raise RuntimeError(f"{scene.code} 场景主图缺失、未确认或已过期")
    specs.append(scene_ref)
    if include_top:
        top = _view_ref(db, project, scene, "top", "scene-top")
        if top:
            top["optional"] = True
            specs.append(top)

    angle = f"{cut.get('cameraPosition') or ''} {cut.get('frame') or ''}"
    preferred = (
        "back-full"
        if any(token in angle for token in ("背面", "背影", "身后"))
        else "side-full"
        if any(token in angle for token in ("侧面", "侧身", "侧拍"))
        else ""
    )
    for character in character_specs_for(db, project, frame):
        asset = _asset_row(db, project, "cast", character["code"])
        if asset is None:
            raise RuntimeError(f"{frame.seg_id} f{frame.cut_index} 找不到角色 {character['code']}")
        anchor = (
            _view_ref(db, project, asset, preferred, "cast-anchor")
            if preferred
            else _ref_from_asset(db, project, asset, "cast-anchor")
        )
        if not anchor:
            anchor = _ref_from_asset(db, project, asset, "cast-anchor")
        if not anchor:
            raise RuntimeError(
                f"{frame.seg_id} f{frame.cut_index} 角色 {asset.code} 的引用图缺失或已过期"
            )
        specs.append(anchor)
        distance = str(cut.get("size") or meta.get("size") or "")
        if not preferred and distance in ("close", "extreme-close", "medium"):
            face = _view_ref(db, project, asset, "face-front", "cast-face")
            if face and face["path"] != anchor["path"]:
                face["optional"] = True
                specs.append(face)

    for prop_code in dict.fromkeys(meta.get("props") or []):
        prop = _asset_row(db, project, "prop", prop_code)
        if prop is None:
            raise RuntimeError(f"{frame.seg_id} f{frame.cut_index} 找不到道具 {prop_code}")
        state = str((cut.get("propStates") or {}).get(prop_code) or "").strip()
        if not state:
            states = _loads(prop.data).get("states") or []
            if len(states) == 1:
                state = str(states[0].get("state") or "")
            elif len(states) > 1:
                raise RuntimeError(f"{prop_code} 存在多个状态，请在分镜 propStates 明确当前状态")
        view = _state_view(prop, state) if state else ""
        ref = _view_ref(db, project, prop, view, "prop") if view else _ref_from_asset(
            db, project, prop, "prop"
        )
        if not ref:
            raise RuntimeError(f"{frame.seg_id} f{frame.cut_index} 道具 {prop_code} 引用图缺失")
        specs.append(ref)
    return specs


def reference_plan_for(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
    seg: Optional[HibtvSegment],
    *,
    include_top: bool = False,
    include_continuity: bool = False,
) -> List[Dict]:
    """返回一个镜头的有效引用素材；用户调整过时以用户清单为准。"""
    meta = _loads(frame.meta)
    if meta.get("refsCustomized"):
        specs = saved_reference_specs(db, project, frame)
        if not specs:
            raise RuntimeError(
                f"{frame.seg_id} f{frame.cut_index} 的自定义引用素材已失效，请重新选择"
            )
    else:
        specs = _auto_reference_specs(
            db, project, frame, seg, include_top=include_top
        )
    if include_continuity:
        previous = _previous_frame_ref(db, project, frame)
        if previous:
            specs = [previous] + specs
    return _dedupe_specs(specs)


def ref_specs_for(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
    seg: Optional[HibtvSegment],
) -> List[Dict]:
    """兼容旧调用：用于可选分镜图的镜头引用。"""
    specs = reference_plan_for(
        db, project, frame, seg, include_top=False, include_continuity=False
    )
    if len(specs) > MAX_IMAGE_REFS:
        required = [row for row in specs if not row.get("optional")]
        optional = [row for row in specs if row.get("optional")]
        specs = required + optional[:max(0, MAX_IMAGE_REFS - len(required))]
    if len(specs) > MAX_IMAGE_REFS:
        raise RuntimeError(
            f"{frame.seg_id} f{frame.cut_index} 必需图片 {len(specs)} 张，"
            f"超过分镜图上限 {MAX_IMAGE_REFS} 张，请减少素材或拆分镜头"
        )
    return specs


def _image_ref_specs(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
    seg: Optional[HibtvSegment],
) -> List[Dict]:
    specs = reference_plan_for(
        db,
        project,
        frame,
        seg,
        include_top=False,
        include_continuity=True,
    )
    required = [row for row in specs if not row.get("optional")]
    optional = [row for row in specs if row.get("optional")]
    return required + optional[:max(0, MAX_IMAGE_REFS - len(required))]


def _previous_frame_ref(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
) -> Optional[Dict]:
    if int(frame.cut_index or 0) <= 1:
        return None
    previous = (
        db.query(HibtvFrame)
        .filter(
            HibtvFrame.project_id == project.id,
            HibtvFrame.seg_id == frame.seg_id,
            HibtvFrame.cut_index == int(frame.cut_index) - 1,
        )
        .first()
    )
    if previous is None or not previous.path or not previous.upload_id:
        return None
    if not frame_current(db, project, previous):
        return None
    from .hibtv_jobs import abs_path

    path = abs_path(project.id, previous.path)
    if not path.is_file():
        return None
    return {
        "assetId": int(previous.id),
        "kind": "continuity",
        "code": f"{frame.seg_id}-f{int(previous.cut_index)}",
        "name": "上一镜已确认画面",
        "path": path,
        "role": "continuity-frame",
        "view": "frame",
        "note": "只锁定人物、服装、位置与前一镜结束状态，不复制边框或拼贴",
        "uploadId": int(previous.upload_id),
    }


def ensure_scene_main(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
    seg: Optional[HibtvSegment],
) -> Optional[Path]:
    code = _scene_code(db, project, int(frame.ep), seg)
    if not code:
        raise RuntimeError(
            f"{frame.seg_id} f{int(frame.cut_index)} 未解析到场景 code，不能生成分镜图"
        )
    existing = _asset_main(db, project, "scene", code)
    if existing is None:
        raise RuntimeError(
            f"{frame.seg_id} f{int(frame.cut_index)} 场景 {code} 请先生成并确认当前主图"
        )
    return existing


def refs_for(
    db: Session,
    project: HibtvProject,
    frame: HibtvFrame,
    seg: Optional[HibtvSegment],
) -> List[Path]:
    return [Path(row["path"]) for row in ref_specs_for(db, project, frame, seg)]


def _reference_line(index: int, ref: Dict) -> str:
    tag = f"<image{index}>"
    role = ref.get("role")
    code = str(ref.get("code") or "")
    name = str(ref.get("name") or "")
    identity = code if not name or name == code else f"{code} {name}"
    note = str(ref.get("note") or "").strip()
    if role == "scene-top":
        text = (
            f"{tag} is the top-down staging layout for {identity}. "
            "Use only its room topology and relative subject positions; "
            "do not copy its overhead camera angle into the output."
        )
    elif role == "scene":
        text = (
            f"{tag} is the environment reference for {identity}. "
            "Follow its architecture, layout, materials, colours and lighting. "
            "Do not turn it into a camera-angle requirement and do not add people from it."
        )
    elif role == "cast-face":
        text = (
            f"{tag} is the face reference for {identity}. "
            "It is the SAME person as their full-body reference, not another character."
        )
    elif role == "cast-anchor":
        text = (
            f"{tag} is the identity, body and costume reference for {identity}. "
            "Keep this person's exact identity and clothing; do not duplicate the person."
        )
    elif role == "prop":
        text = (
            f"{tag} is the current prop reference for {identity}. "
            "Follow its shape, material, colour and state."
        )
    elif role == "continuity-frame":
        text = (
            f"{tag} is the accepted previous shot. Preserve its character identity, "
            "costume, current position, eyelines and lighting continuity; it is not a panel to copy."
        )
    else:
        text = f"{tag} is a reference for {identity}."
    return f"{text} {note}".strip()


def prompt_for(
    frame: HibtvFrame,
    lighting: str = "",
    *,
    characters: Optional[List[Dict]] = None,
    refs: Optional[List[Dict]] = None,
    tail: str = "",
) -> str:
    """生成 Qwen Image 2.1 单画面提示词，参考图使用 ``<imageN>``。"""
    meta = _loads(frame.meta)
    cut = meta.get("cut") or meta
    base = str(
        meta.get("overridePrompt") or cut.get("frame") or frame.prompt or ""
    ).strip()
    parts: List[str] = [
        "Create exactly one clean full-frame cinematic still, not a contact sheet, "
        "not a character sheet, not a split screen and not a multi-view layout."
    ]
    if base:
        parts.append(base)
    if lighting:
        parts.append(f"Lighting: {lighting}.")
    for key in COMPOSITION_FIELDS:
        if cut.get(key):
            parts.append(f"{key}: {cut[key]}.")
    if meta.get("blocking"):
        parts.append(f"Scene blocking: {meta['blocking']}.")
    if cut.get("state"):
        parts.append(f"Current state: {cut['state']}.")

    visible = characters or []
    if visible:
        names = ", ".join(
            item["code"]
            if not item.get("name") or item.get("name") == item["code"]
            else f"{item['code']} {item['name']}"
            for item in visible
        )
        parts.append(
            f"Visible named people: {names}. Show each exactly once as a different person; "
            "do not clone, mirror or merge faces."
        )
    extras = cut.get("extras", meta.get("extras"))
    if extras is not None:
        total = len(visible) + max(0, int(extras))
        parts.append(
            f"Exact on-screen person count: {total}. "
            "Do not add background people unless this count requires them."
        )
    elif not visible:
        parts.append("This shot has no named people. Do not add invented foreground people.")

    ref_rows = [row for row in (refs or []) if row.get("path")]
    for index, ref in enumerate(ref_rows, start=1):
        parts.append(_reference_line(index, ref))
    parts.append(tail or FRAME_TAIL)
    return "\n".join(part for part in parts if part)


async def draw(db: Session, project: HibtvProject, frame: HibtvFrame) -> Dict:
    """生成一张可选分镜图；不再冒充视频生成的必选中间层。"""
    from .hibtv_jobs import abs_path, jdumps, now, project_dir, register_upload

    payload = _loads(frame.meta)
    seg = (
        db.query(HibtvSegment)
        .filter(
            HibtvSegment.project_id == project.id,
            HibtvSegment.seg_id == frame.seg_id,
        )
        .first()
    )
    lighting = payload.get("lighting") or lighting_of(
        db,
        project,
        int(frame.ep),
        int((_loads(seg.data).get("sceneIndex") if seg else 0) or 0),
    )
    ensure_scene_main(db, project, frame, seg)
    characters = character_specs_for(db, project, frame)
    ref_specs = _image_ref_specs(db, project, frame, seg)
    if len(ref_specs) > MAX_IMAGE_REFS:
        raise RuntimeError(
            f"{frame.seg_id} f{frame.cut_index} 分镜图需要 {len(ref_specs)} 张参考图，"
            f"超过上限 {MAX_IMAGE_REFS} 张"
        )
    cast_codes = {row["code"] for row in ref_specs if row.get("kind") == "cast"}
    missing = [row["code"] for row in characters if row["code"] not in cast_codes]
    if missing:
        raise RuntimeError(
            f"{frame.seg_id} f{frame.cut_index} 画面人物 {missing} 缺少当前角色引用图"
        )

    prompt = prompt_for(
        frame,
        str(lighting or ""),
        characters=characters,
        refs=ref_specs,
        tail=frame_tail(project),
    )
    width, height = canvas(project)
    rel = rel_for(frame)
    out_abs = project_dir(project.id) / rel
    expected_plan = payload.get("planHash")
    expected_context = context_hash(db, project, frame.ep)
    expected_refs = [int(row["uploadId"]) for row in ref_specs]
    result = await hibtv_image.generate_image(
        prompt=prompt,
        out_path=out_abs,
        ref_paths=[Path(row["path"]) for row in ref_specs],
        width=width,
        height=height,
        negative=NEGATIVE,
        grid=True,
    )
    if not out_abs.is_file():
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 分镜图没有落盘")

    db.expire_all()
    db.refresh(frame)
    current_refs = _image_ref_specs(db, project, frame, seg)
    if (
        _loads(frame.meta).get("planHash") != expected_plan
        or context_hash(db, project, frame.ep) != expected_context
        or [int(row["uploadId"]) for row in current_refs] != expected_refs
    ):
        raise RuntimeError("生成期间分镜或引用素材已改变，请按当前输入重新生成")

    frame.path = rel
    frame.upload_id = register_upload(db, project.user_id, out_abs) or 0
    frame.version = int(frame.version or 0) + 1
    frame.status = "review"
    frame.updated_at = now()
    payload.update(
        {
            "size": [width, height],
            "seed": result.get("seed") or 0,
            "at": now().strftime("%Y-%m-%d %H:%M"),
            "cost": payload.get("cost") or 0,
            "uploaded": False,
            "renderPrompt": prompt,
            "renderedPlanHash": payload.get("planHash"),
            "contextHash": context_hash(db, project, frame.ep),
            "stale": False,
            "sha256": file_hash(out_abs),
            "dependencies": [
                {"path": str(row["path"]), "sha256": file_hash(row["path"])}
                for row in ref_specs
            ],
            "referenceIds": expected_refs,
            "imageMode": "hibtv-single-frame",
            "imageWorkflow": str(hibtv_image.TEMPLATE_PATH),
        }
    )
    frame.meta = jdumps(payload)
    db.flush()
    return {"path": rel, "upload_id": int(frame.upload_id or 0), "version": frame.version}


def _public_ref(ref: Dict, project_id: int) -> Dict:
    from .hibtv_jobs import project_dir

    url = ""
    try:
        rel = Path(ref["path"]).resolve().relative_to(project_dir(project_id).resolve()).as_posix()
        url = f"/files/hibtv/{project_id}/{rel}"
    except (KeyError, OSError, ValueError):
        pass
    return {
        "assetId": int(ref.get("assetId") or 0),
        "kind": str(ref.get("kind") or ""),
        "code": str(ref.get("code") or ""),
        "name": str(ref.get("name") or ""),
        "role": str(ref.get("role") or ""),
        "view": str(ref.get("view") or ""),
        "note": str(ref.get("note") or ""),
        "uploadId": int(ref.get("uploadId") or 0),
        "url": url,
    }


def plan(
    db: Session,
    project: HibtvProject,
    seg_id: str,
    ep: Optional[int] = None,
) -> List[Dict]:
    query = db.query(HibtvFrame).filter(HibtvFrame.project_id == project.id)
    query = query.filter(HibtvFrame.seg_id == seg_id)
    if ep:
        query = query.filter(HibtvFrame.ep == int(ep))
    seg = (
        db.query(HibtvSegment)
        .filter(
            HibtvSegment.project_id == project.id,
            HibtvSegment.seg_id == seg_id,
        )
        .first()
    )
    if seg is None or _loads(seg.data).get("active") is False:
        return []
    count = len(_loads(seg.data).get("cuts") or [])
    rows = (
        query.filter(HibtvFrame.cut_index <= count)
        .order_by(HibtvFrame.cut_index)
        .all()
    )
    out: List[Dict] = []
    for row in rows:
        if _loads(row.meta).get("active") is False:
            continue
        rel = (row.path or "").strip()
        valid = frame_current(db, project, row)
        references = []
        try:
            references = reference_plan_for(
                db, project, row, seg, include_top=False, include_continuity=False
            )
        except RuntimeError:
            pass
        public_refs = [_public_ref(ref, project.id) for ref in references]
        out.append(
            {
                "id": row.id,
                "ep": row.ep,
                "segId": row.seg_id,
                "prompt": row.prompt or "",
                "version": int(row.version or 0),
                "meta": _loads(row.meta),
                "n": int(row.cut_index),
                "cutIndex": int(row.cut_index),
                "frameId": row.id,
                "uploadId": int(row.upload_id or 0),
                "path": rel,
                "url": f"/files/hibtv/{project.id}/{rel}" if rel else "",
                "status": row.status if valid else ("stale" if rel else "empty"),
                "ready": valid,
                "references": public_refs,
                "refsCustomized": bool(_loads(row.meta).get("refsCustomized")),
            }
        )
    return out


def missing(
    db: Session,
    project: HibtvProject,
    seg_id: str,
    ep: Optional[int] = None,
) -> List[int]:
    return [row["cutIndex"] for row in plan(db, project, seg_id, ep) if not row["ready"]]


def context_hash(db: Session, project: HibtvProject, ep: int) -> str:
    from .hibtv_jobs import get_doc, jloads

    return digest(
        {
            "ratio": project.aspect_ratio,
            "style": project.style,
            "imageWorkflow": str(hibtv_image.TEMPLATE_PATH),
            "documents": {
                kind: digest(jloads(doc.data, {})) if doc else "missing"
                for kind in ("cast", "art", "script", "staging")
                for doc in [
                    get_doc(
                        db,
                        project.id,
                        kind,
                        ep if kind in ("script", "staging") else 0,
                    )
                ]
            },
        }
    )


def frame_current(db: Session, project: HibtvProject, frame: HibtvFrame) -> bool:
    from .hibtv_jobs import abs_path

    meta = _loads(frame.meta)
    if (
        not frame.path
        or not frame.upload_id
        or meta.get("stale")
        or meta.get("active") is False
        or not meta.get("planHash")
        or meta.get("renderedPlanHash") != meta.get("planHash")
        or meta.get("contextHash") != context_hash(db, project, frame.ep)
    ):
        return False
    upload = db.get(Upload, int(frame.upload_id))
    path = abs_path(project.id, frame.path)
    if (
        upload is None
        or upload.user_id != project.user_id
        or Path(upload.path).resolve() != path.resolve()
        or not path.is_file()
        or meta.get("sha256") != file_hash(path)
        or not dependencies_valid(meta.get("dependencies", []))
    ):
        return False
    if not meta.get("uploaded"):
        seg = (
            db.query(HibtvSegment)
            .filter(
                HibtvSegment.project_id == project.id,
                HibtvSegment.seg_id == frame.seg_id,
            )
            .first()
        )
        try:
            current = _image_ref_specs(db, project, frame, seg)
        except RuntimeError:
            return False
        if meta.get("referenceIds") != [int(row["uploadId"]) for row in current]:
            return False
    return True


def ensure_ready(
    db: Session,
    project: HibtvProject,
    seg_id: str,
    ep: Optional[int] = None,
) -> List[Dict]:
    """可选分镜图模式的严格校验；路线 A 不调用此函数。"""
    rows = plan(db, project, seg_id, ep)
    seg = (
        db.query(HibtvSegment)
        .filter(
            HibtvSegment.project_id == project.id,
            HibtvSegment.seg_id == seg_id,
        )
        .first()
    )
    count = len(_loads(seg.data).get("cuts") or []) if seg else 0
    if not count or [row["cutIndex"] for row in rows] != list(range(1, count + 1)):
        raise RuntimeError(f"{seg_id} 当前分镜图清单与镜头不一致，请重新导入分镜")
    lack = [row["cutIndex"] for row in rows if not row["ready"]]
    if lack:
        raise RuntimeError(
            f"{seg_id} 缺失或过期分镜图 f{', f'.join(map(str, lack))}，补齐后才能进入关键帧模式"
        )
    return rows


def _dedupe_specs(items: Iterable[Dict]) -> List[Dict]:
    out: List[Dict] = []
    seen = set()
    for item in items:
        key = (
            int(item.get("assetId") or 0),
            str(item.get("view") or ""),
            str(item.get("role") or ""),
            int(item.get("uploadId") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _segment_continuity_ref(
    db: Session,
    project: HibtvProject,
    seg: HibtvSegment,
) -> Optional[Dict]:
    if not bool(_loads(seg.data).get("linkPrev", True)) or int(seg.idx or 0) <= 0:
        return None
    previous = (
        db.query(HibtvSegment)
        .filter(
            HibtvSegment.project_id == project.id,
            HibtvSegment.ep == seg.ep,
            HibtvSegment.idx < seg.idx,
            HibtvSegment.status != "archived",
        )
        .order_by(HibtvSegment.idx.desc())
        .first()
    )
    if previous is None:
        return None
    from .hibtv_jobs import abs_path, project_dir, register_upload

    versions = _loads_list(previous.versions)
    current = next(
        (
            row
            for row in versions
            if int(row.get("v") or 0) == int(previous.current_version or 0)
            and row.get("path")
        ),
        None,
    )
    if current is None:
        return None
    video = Path(current["path"])
    if not video.is_absolute():
        video = abs_path(project.id, str(current["path"]))
    if not video.is_file() or not shutil.which("ffmpeg"):
        return None
    rel = f"refs/continuity/{previous.seg_id}-v{int(current.get('v') or 0)}.png"
    target = project_dir(project.id) / rel
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-y",
                "-sseof",
                "-0.25",
                "-i",
                str(video),
                "-frames:v",
                "1",
                str(target),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode != 0 or not target.is_file():
            logger.warning("提取上一段尾帧失败：%s", result.stderr[-500:])
            return None
    upload = db.query(Upload).filter(Upload.path == str(target)).first()
    upload_id = int(upload.id) if upload else int(register_upload(db, project.user_id, target) or 0)
    return {
        "assetId": int(previous.id),
        "kind": "continuity",
        "code": previous.seg_id,
        "name": "上一段已验收尾帧",
        "path": target,
        "role": "continuity-frame",
        "view": "tail",
        "note": "只承接人物、服装、位置、视线、道具状态和光线；不承接原片构图边框",
        "uploadId": upload_id,
    }


def route_a_reference_plan(
    db: Session,
    project: HibtvProject,
    seg: HibtvSegment,
) -> List[Dict]:
    """汇总整段每个镜头的素材，形成一次视频请求的引用清单。"""
    frames = (
        db.query(HibtvFrame)
        .filter(
            HibtvFrame.project_id == project.id,
            HibtvFrame.seg_id == seg.seg_id,
            HibtvFrame.ep == seg.ep,
        )
        .order_by(HibtvFrame.cut_index)
        .all()
    )
    if not frames:
        raise RuntimeError(f"{seg.seg_id} 没有镜头素材行，请重新导入分镜")
    collected: List[Dict] = []
    for frame in frames:
        if _loads(frame.meta).get("active") is False:
            continue
        refs = reference_plan_for(
            db,
            project,
            frame,
            seg,
            include_top=True,
            include_continuity=False,
        )
        cast_codes = {row["code"] for row in refs if row.get("kind") == "cast"}
        missing = [
            row["code"]
            for row in character_specs_for(db, project, frame)
            if row["code"] not in cast_codes
        ]
        if missing:
            raise RuntimeError(
                f"{seg.seg_id} 第 {frame.cut_index} 镜人物 {missing} 缺少素材引用"
            )
        collected.extend(refs)
    collected = _dedupe_specs(collected)
    continuity = _segment_continuity_ref(db, project, seg)
    if continuity:
        top_exists = any(row.get("role") == "scene-top" for row in collected)
        collected = [
            row
            for row in collected
            if not (top_exists and row.get("role") == "scene")
        ]
        collected.insert(0, continuity)
    collected.sort(key=lambda row: ROLE_PRIORITY.get(str(row.get("role")), 99))
    if len(collected) > MAX_VIDEO_REFS:
        faces = [row for row in collected if row.get("role") == "cast-face"]
        for row in reversed(faces):
            if len(collected) <= MAX_VIDEO_REFS:
                break
            collected.remove(row)
    if len(collected) > MAX_VIDEO_REFS:
        roles = "、".join(
            f"{row.get('code')}:{row.get('role')}" for row in collected
        )
        raise RuntimeError(
            f"{seg.seg_id} 汇总后需要 {len(collected)} 张参考图，超过模型上限 "
            f"{MAX_VIDEO_REFS} 张；请移除不必要素材或拆段。当前顺序：{roles}"
        )
    return collected


def segment_video_refs(
    db: Session,
    project: HibtvProject,
    seg: HibtvSegment,
) -> Tuple[List[int], str]:
    refs = route_a_reference_plan(db, project, seg)
    ids = [int(row["uploadId"]) for row in refs if int(row.get("uploadId") or 0)]
    if len(ids) != len(refs):
        raise RuntimeError(f"{seg.seg_id} 有引用素材缺少 Upload 记录")
    return ids, "assets" if ids else "none"


def _h3_sections(prompt: str) -> Tuple[List[str], str, str]:
    text = str(prompt or "").strip()
    body_match = re.search(
        r"(?:integrated_multimodal_description:|整体视听描述：)\s*(.*?)"
        r"(?=\n(?:overall_soundscape:|整体音景：|non_diegetic_music:|非叙事配乐：)|\Z)",
        text,
        flags=re.S | re.I,
    )
    body = body_match.group(1).strip() if body_match else ""
    chunks = re.split(r"(?m)(?=\[(?:Shot|镜头)\s*\d+\])", body)
    shots = [chunk.strip() for chunk in chunks if re.match(r"\[(?:Shot|镜头)\s*\d+\]", chunk.strip())]
    sound = re.search(
        r"(?:overall_soundscape:|整体音景：)\s*(.*?)(?=\n(?:non_diegetic_music:|非叙事配乐：)|\Z)",
        text,
        flags=re.S | re.I,
    )
    music = re.search(
        r"(?:non_diegetic_music:|非叙事配乐：)\s*(.*)\Z",
        text,
        flags=re.S | re.I,
    )
    return shots, (sound.group(1).strip() if sound else ""), (
        music.group(1).strip() if music else ""
    )


def _shot_body(section: str) -> str:
    body = re.sub(r"^\[(?:Shot|镜头)\s*\d+\]\s*", "", section).strip()
    body = re.sub(r"^At\s+00:\d{2}(?:\.\d{3})?,\s*", "", body, flags=re.I)
    body = re.sub(r"^于\s*00:\d{2}(?:\.\d{3})?，\s*", "", body)
    body = re.sub(r"</?Picture\s+\d+>", "", body, flags=re.I)
    return body.strip()


def route_a_prompt(
    db: Session,
    project: HibtvProject,
    seg: HibtvSegment,
    refs: Optional[List[Dict]] = None,
) -> str:
    """把已校验分镜动作与真实发送素材编译成路线 A 的 H3 Ref2VA 提示词。"""
    refs = refs or route_a_reference_plan(db, project, seg)
    data = _loads(seg.data)
    cuts = data.get("cuts") or []
    sections, sound, music = _h3_sections(str(data.get("h3Prompt") or ""))
    definitions = []
    retention = []
    for index, ref in enumerate(refs, start=1):
        definitions.append(_reference_line(index, ref).replace("<image", "<Picture "))
        retention.append(
            f"<Picture {index}> ({ref.get('code')} / {ref.get('role')}): fully_preserved "
            "only for the role stated in subject_definitions."
        )
    starts: List[float] = []
    cursor = 0.0
    for cut in cuts:
        starts.append(cursor)
        cursor += float(cut.get("seconds") or 0)
    shot_lines: List[str] = []
    for index, cut in enumerate(cuts, start=1):
        source = sections[index - 1] if index <= len(sections) else ""
        body = _shot_body(source)
        if not body:
            body = str(cut.get("shot") or "").strip()
        if not body:
            body = (
                f"{cut.get('camera') or 'Static Shot'}；"
                f"{cut.get('size') or 'medium'}；动作按剧本完成。"
            )
        composition = "；".join(
            f"{key}={cut[key]}"
            for key in COMPOSITION_FIELDS
            if cut.get(key)
        )
        if composition:
            body = f"{body}（{composition}）"
        timing = f"{starts[index - 1]:.3f}"
        shot_lines.append(f"[镜头 {index}] 于 {timing} 秒，{body}")
    summary = f"[reference generation] 一段 {len(cuts)} 镜、{cursor:.3f} 秒的连续短剧视频。"
    definition_text = "\n".join(definitions)
    retention_text = "\n".join(retention)
    sound_text = sound or str(data.get("soundscape") or "保持现场环境声与动作声。")
    music_text = music or str(data.get("music") or "无")
    return (
        "subject_definitions:\n"
        f"{definition_text}\n\n"
        "summary:\n"
        f"{summary} Reference images define environment, layout, identity and props only; "
        "they are not timeline cut anchors.\n\n"
        "retention_analysis:\n"
        f"{retention_text}\n\n"
        f"detailed_description:\n段级开场走位：{data.get('blocking') or '按各镜描述保持人物关系'}\n"
        + "\n".join(shot_lines)
        + "\n\n"
        f"overall_soundscape: {sound_text}\n\n"
        f"non_diegetic_music: {music_text}"
    ).strip()


def video_input_hash(
    db: Session,
    project: HibtvProject,
    seg: HibtvSegment,
    *,
    refs: Optional[List[Dict]] = None,
    prompt: Optional[str] = None,
) -> str:
    from .hibtv_contracts import segment_input

    refs = refs if refs is not None else route_a_reference_plan(db, project, seg)
    prompt = prompt if prompt is not None else route_a_prompt(db, project, seg, refs)
    return digest(
        {
            "segment": segment_input(_loads(seg.data)),
            "prompt": prompt,
            "references": [
                {
                    "upload": int(row.get("uploadId") or 0),
                    "path": str(row.get("path")),
                    "role": row.get("role"),
                    "sha256": file_hash(row["path"]) if Path(row["path"]).is_file() else "",
                }
                for row in refs
            ],
            "aspectRatio": project.aspect_ratio,
            "resolution": "768p",
            "route": "A-assets",
        }
    )

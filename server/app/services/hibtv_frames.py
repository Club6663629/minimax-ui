"""关键帧出图：**每切一张，f1..fN**（ref2v 首选的图源，路 A）。

口径（skills frame.md 官方挂图合同）：
- 提示词 = `cut.frame`（分镜给的关键帧描述）+ 该场 `lighting` + 每张参考图用途声明
  + 人物此刻位置状态 + 按画幅的末尾固定句；
- 参考图 = 场景锚点 + 每个在场角色的锚点(front-full)+大头照(face-front) + 在场道具锚点；
  都是**干净单视图**，绝不挂拼格 sheet / 多视图网格；
- 画布按**输出网格**出：9:16 → 768×1344，16:9 → 1344×768（`comfyui.SOURCE_SIZES`）；
- 落 `frames/{seg}/f{n}.png` 并登记 Upload（出片时按 `picture` 序号挂给 H3）；
- 缺帧或依赖过期必须阻断出片；资产图不替代按切点编号的分镜图。
"""
import json
import logging
from uuid import uuid4
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import HibtvAsset, HibtvFrame, HibtvProject, HibtvSegment, Upload
from . import comfyui, hibtv_assets, hibtv_image
from .hibtv_contracts import (digest, file_hash, COMPOSITION_FIELDS, dependencies_valid, frame_plan)

logger = logging.getLogger(__name__)

# 场景 1 + 每个在场角色（锚点+大头照）2 + 道具；短剧单场人数有限，上限放到 6。
# 多图模型会把多张角色卡误当成同一人物多视图，prompt_for() 必须逐槽声明身份与用途。
MAX_REFS = 6
# 画面硬约束：禁屏内文字（像素约束，不是内容）。末尾固定句照 frame.md。
FRAME_TAIL = "画面里不出现字幕、水印或任何叠加文字"
CLEAN_TAIL = "No text, no watermark, no borders — a single clean full-bleed frame."
NEGATIVE = ("text, subtitle, watermark, logo, signature, caption, border, frame, "
            "multiple panels, split screen, contact sheet, collage, "
            "duplicate person, cloned face, identical twins, two identical men, "
            "extra person, character sheet, multi-view grid")


def frame_tail(project: HibtvProject) -> str:
    """按项目画幅生成末尾固定句（16:9 横屏 / 9:16 竖屏），叠加 frame.md 官方英文固定句。"""
    ratio = (project.aspect_ratio or "9:16").strip()
    orient = "横屏构图（16:9）" if ratio.startswith("16") else "竖屏构图（9:16）"
    return f"{orient}，{FRAME_TAIL}。{CLEAN_TAIL}"


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
    return f"frames/{frame.seg_id}/f{int(frame.cut_index)}-{uuid4().hex}.png"


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


def prompt_for(frame: HibtvFrame, lighting: str = "", *,
               characters: Optional[List[Dict]] = None,
               refs: Optional[List[Dict]] = None,
               tail: str = "") -> str:
    """生成关键帧 prompt，并把每个参考图槽位绑定到唯一身份与用途。

    `characters` 按 frame.meta.characters 的顺序传入；`refs` 的每个条目含
    kind/code/name/path/role。多角色时必须明确“不同的人”和“不得复制”，否则 i2i
    模型会把角色卡当成同一主体的多视图并复制其中一张脸。末尾固定句按画幅（tail）。
    """
    meta = _loads(frame.meta)
    base = str(meta.get("overridePrompt") or (meta.get("cut") or {}).get("frame") or frame.prompt or "").strip()
    tail = (tail or FRAME_TAIL).strip()
    parts: List[str] = []

    def add(text: str) -> None:
        text = (text or "").strip()
        if text and not any(text in existing for existing in parts):
            parts.append(text)

    add(base)
    add(lighting)
    cut = meta.get("cut") or {}
    for key in COMPOSITION_FIELDS:
        if cut.get(key):
            add(f"{key}: {cut[key]}")
    add(str(meta.get("blocking") or ""))
    if cut.get("state"):
        add(str(cut["state"]))
    _seen_codes = set()
    rows = []
    for row in (characters or []):
        code = str(row.get("code") or "")
        if code and code not in _seen_codes:
            _seen_codes.add(code)
            rows.append(row)
    ref_rows = [row for row in (refs or []) if row.get("path")]
    if ref_rows:
        mapping = ["参考图用途声明：每张图声明唯一用途；同角色的多视图仍然代表同一个人"]
        for idx, ref in enumerate(ref_rows, start=1):
            role = str(ref.get("role") or ref.get("kind") or "")
            code = ref.get("code", "")
            name = ref.get("name", "")
            if role == "scene":
                mapping.append(
                    f"Picture {idx} = 场景 {code} {name}"
                    "（which is the environment：环境/材质/光的唯一标准，本身不含额外人物）")
            elif role == "cast-face":
                mapping.append(
                    f"Picture {idx} = {code} {name} 的大头照"
                    "（whose face to match：这张只用来对齐脸，与同角色的全身图是同一个人）")
            elif role == "prop":
                mapping.append(
                    f"Picture {idx} = 道具 {code} {name}"
                    "（which prop to match：形状/材质/颜色按这张）")
            else:
                mapping.append(
                    f"Picture {idx} = {code} {name}"
                    f"（{ref.get('gender') or '性别按角色卡'}，"
                    f"{ref.get('identity') or '身份按角色卡'}）")
        add("；".join(mapping) + "。")

    named = len(rows)
    # 人数上限不等于精确人数；群演从当前镜头显式输入，不凭空添加或消除。
    extras = cut.get("extras", meta.get("extras"))
    if rows:
        names = "；".join(f"{row['code']} {row.get('name', '')}" for row in rows)
        add(f"具名人物身份：{names}；每个角色只出现一次，不同角色不得串脸、克隆或分身。")
    if extras is not None:
        add(f"本镜未具名群演 {int(extras)} 人，人数与位置按本镜状态和 blocking。")
    if not named and extras == 0:
        add("本镜为空景，不添加人物。")
    if named:
        # frame.md L22：切镜时人物位置是连续的剧情状态，只写构图不写状态会把人画回上一状态
        add("人物位置状态必须与本镜剧情连续（如已上船 / 在舱内 / 站在桥头），"
            "不要把已进入新位置的人物画回之前的状态。")
    add(tail)
    return "；".join(parts) if parts else tail


def _asset_main(db: Session, project: HibtvProject, kind: str, code: str) -> Optional[Path]:
    from .hibtv_jobs import abs_path

    if not code:
        return None
    asset = (db.query(HibtvAsset)
             .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == kind,
                     HibtvAsset.code == code)
             .first())
    if asset is None or not hibtv_assets.main_current(project, asset):
        return None
    path = abs_path(project.id, asset.main_path)
    return path if path.exists() else None


def _asset_row(db: Session, project: HibtvProject, kind: str,
               code: str) -> Optional[HibtvAsset]:
    if not code:
        return None
    return (db.query(HibtvAsset)
            .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == kind,
                    HibtvAsset.code == code)
            .first())


def _ref_from_asset(db: Session, project: HibtvProject,
                    asset: Optional[HibtvAsset], role: str = "") -> Optional[Dict]:
    from .hibtv_jobs import abs_path

    if asset is None or not (asset.main_path or "").strip():
        return None
    path = abs_path(project.id, asset.main_path)
    if not path.exists():
        return None
    default_role = {"scene": "scene", "prop": "prop",
                    "cast": "cast-anchor"}.get(asset.kind, asset.kind)
    return {"kind": asset.kind, "code": asset.code, "name": asset.name, "path": path,
            "role": role or default_role, "uploadId": int(asset.main_upload_id or 0)}


def _view_ref(db: Session, project: HibtvProject, asset: Optional[HibtvAsset],
              view_id: str, role: str) -> Optional[Dict]:
    """把派生视图当参考（如角色大头照 face-front）：只取干净单视图，绝不取拼格。"""
    from .hibtv_jobs import abs_path

    if asset is None or not hibtv_assets.view_current(project, asset, view_id):
        return None
    item = (_loads(asset.views) or {}).get(view_id) or {}
    rel = str((item or {}).get("path") or "").strip()
    if not rel:
        return None
    path = abs_path(project.id, rel)
    if not path.exists():
        return None
    return {"kind": asset.kind, "code": asset.code, "name": asset.name, "path": path,
            "role": role, "uploadId": int((item or {}).get("uploadId") or 0)}


def character_specs_for(db: Session, project: HibtvProject,
                        frame: HibtvFrame) -> List[Dict]:
    """按 cut.characters 顺序返回本帧角色身份；去重但不改原出场顺序。"""
    out: List[Dict] = []
    seen = set()
    for raw in _loads(frame.meta).get("characters") or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        asset = _asset_row(db, project, "cast", code)
        data = _loads(asset.data) if asset is not None else {}
        persona = data.get("persona") or {}
        out.append({
            "code": code,
            "name": str(data.get("name") or (asset.name if asset else code)),
            "gender": str(persona.get("gender") or ""),
            "identity": str(persona.get("identity") or ""),
        })
    return out


def ref_specs_for(db: Session, project: HibtvProject, frame: HibtvFrame,
                  seg: Optional[HibtvSegment]) -> List[Dict]:
    """先保证环境、每位角色和叙事道具，再分配可选脸图；不可静默裁尾。"""
    meta = _loads(frame.meta)
    cut = meta.get("cut") or meta
    requested = cut.get("referenceViews") or {}
    required, optional = [], []

    def select(asset, role, state="", suggested=""):
        if asset is None:
            raise RuntimeError(f"{frame.seg_id} f{frame.cut_index} 缺少 {role} 资产")
        view = requested.get(asset.code)
        if state:
            spec = next((s for s in hibtv_assets.view_defs(asset.kind, _loads(asset.data))
                         if s.get("state") == state), None)
            if spec is None:
                raise RuntimeError(f"{asset.code} 没有登记状态 {state}")
            view = spec["id"]
        if view and view != hibtv_assets.anchor_id(asset.kind):
            ref = _view_ref(db, project, asset, view, role)
        elif suggested and hibtv_assets.view_current(project, asset, suggested):
            ref = _view_ref(db, project, asset, suggested, role)
        else:
            ref = _ref_from_asset(db, project, asset, role)
        if not ref:
            raise RuntimeError(f"{asset.code} 的 {view or '主图'} 缺失、未确认或已过期")
        required.append(ref)
        return ref

    scene = _asset_row(db, project, "scene", _scene_code(db, project, int(frame.ep), seg))
    lighting = cut.get("sceneState") or lighting_of(db, project, int(frame.ep),
                       int((_loads(seg.data).get("sceneIndex") if seg else 0) or 0))
    select(scene, "scene", state=lighting)
    angle = str(cut.get("cameraPosition") or "") + str(cut.get("frame") or "")
    suggested = "back-full" if any(t in angle for t in ("背面", "背影", "身后")) else (
                "side-full" if any(t in angle for t in ("侧面", "侧身", "侧拍")) else "")
    for code in dict.fromkeys(meta.get("characters") or []):
        asset = _asset_row(db, project, "cast", code)
        ref = select(asset, "cast-anchor", suggested=suggested)
        persona = _loads(asset.data).get("persona") or {}
        ref.update({"gender": persona.get("gender", ""), "identity": persona.get("identity", "")})
        if cut.get("size", meta.get("size")) in ("close", "extreme-close", "medium") and not suggested:
            face = _view_ref(db, project, asset, "face-front", "cast-face")
            if face and face["path"] != ref["path"]:
                optional.append(face)
    for code in dict.fromkeys(meta.get("props") or []):
        asset = _asset_row(db, project, "prop", code)
        state = (cut.get("propStates") or {}).get(code, "")
        if not state:
            states = _loads(asset.data).get("states") or [] if asset else []
            if len(states) == 1:
                state = states[0].get("state", "")
            elif len(states) > 1:
                raise RuntimeError(f"{code} 存在多个状态，请在分镜 propStates 明确当前状态")
        select(asset, "prop", state=state)
    if len(required) > MAX_REFS:
        raise RuntimeError(f"本镜必需参考图 {len(required)} 张超过上限 {MAX_REFS}，请拆镜，不能裁掉道具或角色")
    return required + optional[:MAX_REFS-len(required)]


async def ensure_scene_main(db: Session, project: HibtvProject, frame: HibtvFrame,
                            seg: Optional[HibtvSegment]) -> Optional[Path]:
    """关键帧开工前核验已确认的场景主图，不隐式生成资产。"""
    code = _scene_code(db, project, int(frame.ep), seg)
    if not code:
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 未解析到场景 code，"
                           "不能缺场景锚点出关键帧")
    asset = _asset_row(db, project, "scene", code)
    if asset is None:
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 找不到场景资产 {code}")
    existing = _asset_main(db, project, "scene", code)
    if existing is None or not hibtv_assets.main_current(project, asset):
        raise RuntimeError(f"场景 {code} 请先生成并确认当前主图及所需光照状态图")
    return existing


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
    """该切的参考图：场景主图 + 全部出场角色主图，顺序与身份锁一致。"""
    return [Path(row["path"]) for row in ref_specs_for(db, project, frame, seg)]


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
    await ensure_scene_main(db, project, frame, seg)
    characters = character_specs_for(db, project, frame)
    ref_specs = ref_specs_for(db, project, frame, seg)
    # 人数与参考图必须同源：任一角色缺可用主图、或被 MAX_REFS 截断，都会让 prompt 声明的人数
    # 大于实际可参考的脸 -> 模型猜脸/串脸。此处直接失败，禁止静默省略。
    ref_cast = {str(r.get("code")) for r in ref_specs if r.get("kind") == "cast"}
    missing = [str(c["code"]) for c in characters if str(c["code"]) not in ref_cast]
    if missing:
        raise RuntimeError(
            f"{frame.seg_id} f{int(frame.cut_index)} 角色 {missing} 缺少可用角色主图或超出"
            f"单帧参考图上限 {MAX_REFS} 张（含场景）：先补角色主图或拆分镜头后再出关键帧")
    prompt = prompt_for(frame, str(lighting or ""), characters=characters, refs=ref_specs,
                        tail=frame_tail(project))
    width, height = canvas(project)
    rel = rel_for(frame)
    out_abs = project_dir(project.id) / rel
    old_abs = abs_path(project.id, frame.path) if (frame.path or "").strip() else None
    ref_paths = [p for p in (Path(row["path"]) for row in ref_specs)
                 if p != (old_abs if old_abs and old_abs.exists() else None)]
    expected_plan = payload.get("planHash")
    expected_context = context_hash(db, project, frame.ep)
    expected_refs = [r["uploadId"] for r in ref_specs]
    result = await hibtv_image.generate_image(
        prompt=prompt, out_path=out_abs, ref_paths=ref_paths, width=width, height=height,
        negative=NEGATIVE, keep_adv_negative=True, grid=True)
    if not out_abs.exists():
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 出图没有落盘")

    db.expire_all()
    db.refresh(frame)
    if (_loads(frame.meta).get("planHash") != expected_plan
            or context_hash(db, project, frame.ep) != expected_context
            or _loads(frame.meta).get("overridePrompt") != payload.get("overridePrompt")
            or [r["uploadId"] for r in ref_specs_for(db, project, frame, seg)] != expected_refs):
        raise RuntimeError("生成期间分镜或参考图已改变，请根据当前输入重新生成")
    frame.path = rel
    frame.upload_id = register_upload(db, project.user_id, out_abs) or 0
    frame.refs = jdumps([str(p) for p in ref_paths])
    frame.version = int(frame.version or 0) + 1
    frame.status = "review"
    frame.updated_at = now()
    payload["size"] = [width, height]
    payload["seed"] = result.get("seed") or 0
    payload["at"] = now().strftime("%Y-%m-%d %H:%M")
    payload["cost"] = payload.get("cost") or 0
    payload.update({"uploaded": False, "renderPrompt": prompt, "renderedPlanHash": payload.get("planHash"),
                    "contextHash": context_hash(db, project, frame.ep), "stale": False,
                    "sha256": file_hash(out_abs),
                    "dependencies": [{"path": str(p), "sha256": file_hash(p)} for p in ref_paths],
                    "referenceIds": [r["uploadId"] for r in ref_specs]})
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
    seg = db.query(HibtvSegment).filter(HibtvSegment.project_id == project.id,
              HibtvSegment.seg_id == seg_id).first()
    if seg is None or _loads(seg.data).get("active") is False:
        return []
    count = len(_loads(seg.data).get("cuts") or [])
    rows = query.filter(HibtvFrame.cut_index <= count).order_by(HibtvFrame.cut_index).all()
    out: List[Dict] = []
    for i, row in enumerate(rows, start=1):
        rel = (row.path or "").strip()
        if _loads(row.meta).get("active") is False:
            continue
        valid = frame_current(db, project, row)
        out.append({
            "id": row.id, "ep": row.ep, "segId": row.seg_id,
            "prompt": row.prompt or "", "version": int(row.version or 0), "meta": _loads(row.meta),
            "n": int(row.cut_index), "cutIndex": int(row.cut_index), "frameId": row.id,
            "uploadId": int(row.upload_id or 0), "path": rel,
            "url": f"/files/hibtv/{project.id}/{rel}" if rel else "",
            "status": row.status if valid else ("stale" if rel else "empty"),
            "ready": valid,
        })
    return out


def missing(db: Session, project: HibtvProject, seg_id: str,
            ep: Optional[int] = None) -> List[int]:
    """缺图的切号清单（有切但没出图，或出了图没登记 Upload）。"""
    return [p["cutIndex"] for p in plan(db, project, seg_id, ep)
            if not p.get("ready")]


def context_hash(db, project, ep):
    from .hibtv_jobs import get_doc, jloads
    return digest({"ratio": project.aspect_ratio, "documents": {
        kind: digest(jloads(doc.data, {})) if doc else "missing"
        for kind in ("cast", "art", "script", "staging")
        for doc in [get_doc(db, project.id, kind, ep if kind in ("script", "staging") else 0)]}})


def frame_current(db, project, frame):
    from .hibtv_jobs import abs_path
    meta = _loads(frame.meta)
    if (not frame.path or not frame.upload_id or meta.get("stale")
            or meta.get("active") is False or not meta.get("planHash")
            or meta.get("renderedPlanHash") != meta.get("planHash")
            or meta.get("contextHash") != context_hash(db, project, frame.ep)):
        return False
    upload = db.get(Upload, int(frame.upload_id))
    path = abs_path(project.id, frame.path)
    if (upload is None or upload.user_id != project.user_id or Path(upload.path).resolve() != path.resolve()
            or not path.is_file() or meta.get("sha256") != file_hash(path)
            or not dependencies_valid(meta.get("dependencies", []))):
        return False
    # 参考图确认撤回、主图更新或派生图过期，同样使已生成的帧失效。
    if not meta.get("uploaded"):
        seg = db.query(HibtvSegment).filter(HibtvSegment.project_id == project.id,
                                          HibtvSegment.seg_id == frame.seg_id).first()
        try:
            current_refs = ref_specs_for(db, project, frame, seg)
        except RuntimeError:
            return False
        if meta.get("referenceIds") != [r["uploadId"] for r in current_refs]:
            return False
    return True


def ensure_ready(db: Session, project: HibtvProject, seg_id: str,
                 ep: Optional[int] = None) -> List[Dict]:
    rows = plan(db, project, seg_id, ep)
    seg = db.query(HibtvSegment).filter(HibtvSegment.project_id == project.id,
                                       HibtvSegment.seg_id == seg_id).first()
    count = len(_loads(seg.data).get("cuts") or []) if seg else 0
    if not count or [r["cutIndex"] for r in rows] != list(range(1, count+1)):
        raise RuntimeError(f"{seg_id} 当前关键帧清单与 cuts 不一致，请重新导入分镜")
    lack = [r["cutIndex"] for r in rows if not r["ready"]]
    if lack:
        raise RuntimeError(f"{seg_id} 缺失或过期关键帧 f{', f'.join(map(str, lack))}，补齐才能出片")
    if count > 9:
        raise RuntimeError(f"{seg_id} 超过 9 张参考图，需要先拆段")
    return rows


def segment_video_refs(db: Session, project: HibtvProject,
                       seg: HibtvSegment) -> Tuple[List[int], str]:
    rows = ensure_ready(db, project, seg.seg_id, ep=seg.ep)
    return [int(row["uploadId"]) for row in rows], "frames"


def video_input_hash(db, project, seg):
    from .hibtv_contracts import segment_input
    rows = ensure_ready(db, project, seg.seg_id, seg.ep)
    snapshots = []
    for row in rows:
        frame = db.get(HibtvFrame, row["frameId"])
        snapshots.append({"cut": row["cutIndex"], "upload": row["uploadId"],
                          "sha256": _loads(frame.meta).get("sha256"),
                          "planHash": _loads(frame.meta).get("planHash")})
    return digest({"segment": segment_input(_loads(seg.data)), "frames": snapshots,
                   "aspectRatio": project.aspect_ratio, "resolution": "768p"})

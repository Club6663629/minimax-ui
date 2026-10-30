"""关键帧出图：**每切一张，f1..fN**（ref2v 首选的图源，路 A）。

口径（skills frame.md 官方挂图合同）：
- 提示词 = `cut.frame`（分镜给的关键帧描述）+ 该场 `lighting` + 每张参考图用途声明
  + 人物此刻位置状态 + 按画幅的末尾固定句；
- 参考图 = 场景锚点 + 每个在场角色的锚点(front-full)+大头照(face-front) + 在场道具锚点；
  都是**干净单视图**，绝不挂拼格 sheet / 多视图网格；
- 画布按**输出网格**出：9:16 → 768×1344，16:9 → 1344×768（`comfyui.SOURCE_SIZES`）；
- 落 `frames/{seg}/f{n}.png` 并登记 Upload（出片时按 `picture` 序号挂给 H3）；
- 缺帧不拦出片：无分镜图时出片走路 B 兜底（`fallback_refs`：挂场景/角色/道具锚点）。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import HibtvAsset, HibtvFrame, HibtvProject, HibtvSegment
from . import comfyui, hibtv_assets, hibtv_image

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


def prompt_for(frame: HibtvFrame, lighting: str = "", *,
               characters: Optional[List[Dict]] = None,
               refs: Optional[List[Dict]] = None,
               tail: str = "") -> str:
    """生成关键帧 prompt，并把每个参考图槽位绑定到唯一身份与用途。

    `characters` 按 frame.meta.characters 的顺序传入；`refs` 的每个条目含
    kind/code/name/path/role。多角色时必须明确“不同的人”和“不得复制”，否则 i2i
    模型会把角色卡当成同一主体的多视图并复制其中一张脸。末尾固定句按画幅（tail）。
    """
    base = (frame.prompt or "").strip()
    tail = (tail or FRAME_TAIL).strip()
    parts: List[str] = []

    def add(text: str) -> None:
        text = (text or "").strip()
        if text and not any(text in existing for existing in parts):
            parts.append(text)

    add(base)
    add(lighting)
    _seen_codes = set()
    rows = []
    for row in (characters or []):
        code = str(row.get("code") or "")
        if code and code not in _seen_codes:
            _seen_codes.add(code)
            rows.append(row)
    ref_rows = [row for row in (refs or []) if row.get("path")]
    if ref_rows:
        mapping = ["参考图用途声明：每张图只代表一个用途，不是同一人物的多视图"]
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

    from .hibtv_jobs import jloads as _jloads
    try:
        declared_max = int((_jloads(frame.meta, {}) or {}).get("maxOnScreen") or 0)
    except (TypeError, ValueError):
        declared_max = 0
    named = len(rows)
    # 画面人数 = 去重具名角色数与声明上限取大者：具名角色必全出场，正数 maxOnScreen
    # 表示另有未具名群演，与 H3 视频 prompt（_bind_h3_prompt）口径保持一致。
    count = max(named, max(0, declared_max))
    if named:
        extra = count - named
        extra_note = (
            f"其余 {extra} 人为未具名群演/背景人物，不可辨认，不得与具名角色串脸。"
            if extra > 0 else "")
        ids = "、".join(str(row["code"]) for row in rows)
        names = "；".join(
            f"{row['code']} {row.get('name', '')}"
            f"（{row.get('gender') or '性别按角色卡'}，"
            f"{row.get('identity') or '身份按角色卡'}）"
            for row in rows)
        add(f"画面人数硬约束：maxOnScreen={count}；画面中必须恰好有 {count} 个不同的人，"
            f"具名角色只允许 {names} 出场，{ids} 每人只能出现一次。{extra_note}"
            f"{'这些角色必须是不同的人，' if count > 1 else ''}"
            "不得重复；禁止克隆、分身、相同的脸、双胞胎、额外超出人数的人物、角色设定图、"
            "多视图拼贴。每个角色只允许一张脸和一个身体，"
            "不得把多张角色卡的共同特征融合成一张脸。")
    elif count > 0:
        add(f"画面人数硬约束：maxOnScreen={count}；画面中必须恰好有 {count} 个不同的人，"
            "均为未具名群演/背景人物，不可辨认、不得有清晰主语脸；"
            "禁止克隆、分身、相同的脸、额外超出人数的人物。")
    else:
        add("画面人数硬约束：maxOnScreen=0；画面中不得出现完整人物或可辨认人脸，"
            "禁止凭空添加人物、额外人物、克隆或分身。")
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
    if asset is None or not (asset.main_path or "").strip():
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

    if asset is None:
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
    """该切的参考图（与 generate_image(image_names) 完全同序）：

    场景锚点 → 每个在场角色的锚点(front-full)+大头照(face-front) → 在场道具锚点。
    都是干净单视图；越需精准越靠前，总数受 MAX_REFS 约束（超出按顺序裁尾）。
    """
    out: List[Dict] = []
    scene = _asset_row(db, project, "scene", _scene_code(db, project, int(frame.ep), seg))
    scene_ref = _ref_from_asset(db, project, scene, role="scene")
    if scene_ref:
        out.append(scene_ref)
    meta = _loads(frame.meta)
    for raw in meta.get("characters") or []:
        code = str(raw or "").strip()
        if not code or any(row.get("code") == code and row.get("kind") == "cast"
                           for row in out):
            continue
        asset = _asset_row(db, project, "cast", code)
        anchor = _ref_from_asset(db, project, asset, role="cast-anchor")
        if anchor:
            data = _loads(asset.data)
            persona = data.get("persona") or {}
            anchor["gender"] = str(persona.get("gender") or "")
            anchor["identity"] = str(persona.get("identity") or "")
            out.append(anchor)
            # 大头照：只给全身锚点脸会走样，加一张大头照脸最接近（character-refs SKILL.md）
            face = _view_ref(db, project, asset, "face-front", role="cast-face")
            if face:
                face["gender"] = anchor["gender"]
                face["identity"] = anchor["identity"]
                out.append(face)
    # 道具：cut.props 里每个道具的锚点（此前完全被忽略 → 每帧发明新道具）
    for raw in meta.get("props") or []:
        code = str(raw or "").strip()
        if not code or any(row.get("code") == code and row.get("kind") == "prop"
                           for row in out):
            continue
        prop = _asset_row(db, project, "prop", code)
        pref = _ref_from_asset(db, project, prop, role="prop")
        if pref:
            out.append(pref)
    return out[:MAX_REFS]


async def ensure_scene_main(db: Session, project: HibtvProject, frame: HibtvFrame,
                            seg: Optional[HibtvSegment]) -> Optional[Path]:
    """关键帧开工前确保场景主图存在；没有就先用场景卡同一套图片模型生成。"""
    code = _scene_code(db, project, int(frame.ep), seg)
    if not code:
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 未解析到场景 code，"
                           "不能缺场景锚点出关键帧")
    asset = _asset_row(db, project, "scene", code)
    if asset is None:
        raise RuntimeError(f"{frame.seg_id} f{int(frame.cut_index)} 找不到场景资产 {code}")
    existing = _asset_main(db, project, "scene", code)
    if existing is not None:
        return existing
    from . import hibtv_assets

    logger.info("关键帧引用缺场景主图，先生成 %s：%s", code, asset.name)
    await hibtv_assets.draw(db, project, asset)
    generated = _asset_main(db, project, "scene", code)
    if generated is None:
        raise RuntimeError(f"场景 {code} 生成后仍没有可读主图")
    return generated


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


# 视频参考图上限：seedance-prompt.md 建议 4–5 个、不要用满（多了模型分不清优先级）。
VIDEO_MAX_REFS = 5


def fallback_refs(db: Session, project: HibtvProject,
                  seg: HibtvSegment) -> List[Dict]:
    """路 B 兜底：无分镜图时，段的参考图 = 场景/角色/道具的**干净锚点**。

    顺序 场景 → 人物(锚点+大头照) → 道具（seedance-prompt.md）；视频绝不吃多视图网格。
    """
    frames = (db.query(HibtvFrame)
              .filter(HibtvFrame.project_id == project.id, HibtvFrame.seg_id == seg.seg_id)
              .order_by(HibtvFrame.cut_index).all())
    char_codes: List[str] = []
    prop_codes: List[str] = []
    for fr in frames:
        meta = _loads(fr.meta)
        for c in meta.get("characters") or []:
            c = str(c or "").strip()
            if c and c not in char_codes:
                char_codes.append(c)
        for p in meta.get("props") or []:
            p = str(p or "").strip()
            if p and p not in prop_codes:
                prop_codes.append(p)
    out: List[Dict] = []
    scene = _asset_row(db, project, "scene", _scene_code(db, project, int(seg.ep), seg))
    sr = _ref_from_asset(db, project, scene, role="scene")
    if sr and sr.get("uploadId"):
        out.append(sr)
    for code in char_codes:
        asset = _asset_row(db, project, "cast", code)
        anchor = _ref_from_asset(db, project, asset, role="cast-anchor")
        if anchor and anchor.get("uploadId"):
            out.append(anchor)
        face = _view_ref(db, project, asset, "face-front", role="cast-face")
        if face and face.get("uploadId"):
            out.append(face)
    for code in prop_codes:
        prop = _asset_row(db, project, "prop", code)
        pr = _ref_from_asset(db, project, prop, role="prop")
        if pr and pr.get("uploadId"):
            out.append(pr)
    return out[:VIDEO_MAX_REFS]


def segment_video_refs(db: Session, project: HibtvProject,
                       seg: HibtvSegment) -> Tuple[List[int], str]:
    """出片参考图 id：每切都有分镜图走路 A（f1..fN），否则走路 B（锚点兜底）。

    返回 (ref_ids, source)，source ∈ {"frames", "assets", "none"}。两者都空时回落 t2v。
    """
    frames = plan(db, project, seg.seg_id, ep=seg.ep)
    if frames and all(f["path"] and f["uploadId"] for f in frames):
        if len(frames) <= 9:
            return [int(f["uploadId"]) for f in frames], "frames"
    fb = fallback_refs(db, project, seg)
    if fb:
        return [int(r["uploadId"]) for r in fb], "assets"
    ready = [int(f["uploadId"]) for f in frames if f["path"] and f["uploadId"]]
    if ready:
        return ready[:9], "frames"
    return [], "none"

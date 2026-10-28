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

# 场景 1 + 全部出场角色；短剧单场最多 3 人，所以上限是 4。
# 多图模型会把多张角色卡误当成同一人物多视图，prompt_for() 必须逐槽声明身份。
MAX_REFS = 4
# 画面硬约束：竖屏构图 + 禁屏内文字（平台唯一允许追加的两句，属像素约束不是内容）
FRAME_TAIL = "竖屏构图，画面里不出现字幕、水印或任何叠加文字"
NEGATIVE = ("text, subtitle, watermark, logo, signature, caption, border, frame, "
            "multiple panels, split screen, contact sheet, collage, "
            "duplicate person, cloned face, identical twins, two identical men, "
            "extra person, character sheet, multi-view grid")


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
               refs: Optional[List[Dict]] = None) -> str:
    """生成关键帧 prompt，并把每个参考图槽位绑定到唯一角色身份。

    `characters` 按 frame.meta.characters 的顺序传入；`refs` 的每个条目含
    kind/code/name/path。多角色时必须明确“不同的人”和“不得复制”，否则 i2i
    模型会把角色卡当成同一主体的多视图并复制其中一张脸。
    """
    base = (frame.prompt or "").strip()
    parts: List[str] = []

    def add(text: str) -> None:
        text = (text or "").strip()
        if text and not any(text in existing for existing in parts):
            parts.append(text)

    add(base)
    add(lighting)
    add(FRAME_TAIL)
    _seen_codes = set()
    rows = []
    for row in (characters or []):
        code = str(row.get("code") or "")
        if code and code not in _seen_codes:
            _seen_codes.add(code)
            rows.append(row)
    ref_rows = [row for row in (refs or []) if row.get("path")]
    if ref_rows:
        mapping = ["参考图身份锁：每张角色卡只代表一个角色，不是同一人物的多视图"]
        for idx, ref in enumerate(ref_rows, start=1):
            if ref.get("kind") == "scene":
                mapping.append(
                    f"Picture {idx} = 场景 {ref.get('code')} {ref.get('name', '')}"
                    "（环境/空间一致性锚点，本身不含额外人物）")
            else:
                mapping.append(
                    f"Picture {idx} = {ref.get('code')} {ref.get('name', '')}"
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


def _asset_row(db: Session, project: HibtvProject, kind: str,
               code: str) -> Optional[HibtvAsset]:
    if not code:
        return None
    return (db.query(HibtvAsset)
            .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == kind,
                    HibtvAsset.code == code)
            .first())


def _ref_from_asset(db: Session, project: HibtvProject,
                    asset: Optional[HibtvAsset]) -> Optional[Dict]:
    from .hibtv_jobs import abs_path

    if asset is None or not (asset.main_path or "").strip():
        return None
    path = abs_path(project.id, asset.main_path)
    if not path.exists():
        return None
    return {"kind": asset.kind, "code": asset.code, "name": asset.name, "path": path}


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
    """返回与 generate_image(image_names) 完全同序的场景/角色参考图说明。"""
    out: List[Dict] = []
    scene = _asset_row(db, project, "scene", _scene_code(db, project, int(frame.ep), seg))
    scene_ref = _ref_from_asset(db, project, scene)
    if scene_ref:
        out.append(scene_ref)
    for raw in _loads(frame.meta).get("characters") or []:
        code = str(raw or "").strip()
        if not code or any(row.get("code") == code and row.get("kind") == "cast"
                           for row in out):
            continue
        asset = _asset_row(db, project, "cast", code)
        ref = _ref_from_asset(db, project, asset)
        if ref:
            data = _loads(asset.data)
            persona = data.get("persona") or {}
            ref["gender"] = str(persona.get("gender") or "")
            ref["identity"] = str(persona.get("identity") or "")
            out.append(ref)
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
    prompt = prompt_for(frame, str(lighting or ""), characters=characters, refs=ref_specs)
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

"""短剧工作台：作业执行器（worker 常驻循环调用）。

作业种类（`hibtv_jobs.kind`）：
- outline / cast / art / script / storyboard ── LLM（或确定性骨架）产出文档 + 质量门
- images ── 锚点图 / 派生视图出图（qwen21 节点，串行）
- compose ── 整集 ffmpeg 拼接

所有状态机都落在 `hibtv_jobs` 上，前端 4s 轮询；已产出物不受取消影响（与原型一致）。
"""
import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import HIBTV_DIR, settings
from ..database import SessionLocal
from ..models import (HibtvAsset, HibtvChange, HibtvDoc, HibtvJob, HibtvProject, HibtvRender,
                      HibtvSegment, Task, Upload, User)
from . import hibtv_compose, hibtv_gates, hibtv_image, hibtv_llm, hibtv_parse
from .billing import add_credits

logger = logging.getLogger(__name__)

# 每类资产的视图定义：[view, 中文名]，第 1 个是锚点
VIEW_DEFS: Dict[str, List[List[str]]] = {
    "cast": [["front", "正面全身"], ["big", "大头"], ["side", "侧面"], ["back", "背面"]],
    "scene": [["front", "正面"], ["top", "俯视"], ["back", "背面"], ["left", "左侧"], ["right", "右侧"]],
    "prop": [["front", "正面"], ["back", "背面"], ["left", "左侧"], ["right", "右侧"], ["top", "俯视"]],
}

VIEW_HINT = {
    "front": "正面全身，站姿自然，完整入画，头顶与脚部留一点余量",
    "big": "同一个人物的面部特写，五官与发型和参考图完全一致",
    "side": "同一个人物的正侧面全身，五官与服装与参考图完全一致",
    "back": "同一个人物的背面全身，发型与服装与参考图一致",
    "top": "同一空间的俯视全景，布局与参考图完全一致",
    "left": "同一空间的左侧视角，陈设与参考图一致",
    "right": "同一空间的右侧视角，陈设与参考图一致",
}

MEDIA = {"image": {".png", ".jpg", ".jpeg", ".webp"}, "video": {".mp4", ".mov", ".webm"},
         "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}}


# ------------------------------------------------------------------ 路径与 JSON
def project_dir(project_id: int) -> Path:
    d = HIBTV_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def abs_path(project_id: int, rel: str) -> Path:
    """把库里的相对路径还原成绝对路径（防目录穿越）。"""
    base = project_dir(project_id).resolve()
    p = (base / (rel or "")).resolve()
    if base not in p.parents and p != base:
        raise ValueError("非法文件路径")
    return p


def rel_path(project_id: int, path: Path) -> str:
    base = project_dir(project_id).resolve()
    return Path(path).resolve().relative_to(base).as_posix()


def collect_media(project_id: int, rel_name: str, src: Path) -> str:
    """把平台产物（Task 成片）收进项目目录，返回相对路径。

    Worker 落在 `DATA_DIR/output/`，不在项目目录内，而 `/files/hibtv/{pid}/{rel}`
    只服务项目目录：不收进来就既不能预览也不能下载。
    """
    if not src.exists():
        return ""
    dest = project_dir(project_id) / rel_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            return rel_name
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)          # 同盘硬链：不占双份空间
        except OSError:
            shutil.copy2(src, dest)     # 跨盘回退复制
    except OSError:
        logger.warning("产物归集失败：%s → %s", src, dest, exc_info=True)
        return ""
    return rel_name


def jloads(raw: Optional[str], default):
    try:
        val = json.loads(raw or "")
    except (TypeError, ValueError):
        return default
    return val if val is not None else default


def jdumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def now() -> datetime:
    return datetime.now()


# ------------------------------------------------------------------ 文档读写
def get_doc(db: Session, project_id: int, kind: str, ep: int = 0) -> Optional[HibtvDoc]:
    return (db.query(HibtvDoc)
            .filter(HibtvDoc.project_id == project_id, HibtvDoc.kind == kind,
                    HibtvDoc.ep == int(ep))
            .first())


def ensure_doc(db: Session, project_id: int, kind: str, ep: int = 0) -> HibtvDoc:
    doc = get_doc(db, project_id, kind, ep)
    if doc is None:
        doc = HibtvDoc(project_id=project_id, kind=kind, ep=int(ep), status="empty")
        db.add(doc)
        db.flush()
    return doc


def save_doc(db: Session, project_id: int, kind: str, ep: int, data: Dict, *,
             gates: Optional[Dict] = None, status: str = "done", error: str = "") -> HibtvDoc:
    doc = ensure_doc(db, project_id, kind, ep)
    doc.data = jdumps(data)
    doc.version = int(doc.version or 0) + 1
    doc.status = status
    doc.error = error
    if gates is not None:
        doc.gates = jdumps(gates)
    doc.updated_at = now()
    # 落盘留档（便于直接喂 vendored 脚本 / 排查）
    try:
        out = project_dir(project_id) / kind / (f"{kind}-E{ep:02d}.json" if ep else f"{kind}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(jdumps(data), encoding="utf-8")
    except OSError:
        logger.warning("短剧文档落盘失败（不影响主流程）", exc_info=True)
    return doc


def doc_payload(doc: HibtvDoc) -> Dict:
    return {
        "kind": doc.kind, "ep": doc.ep, "status": doc.status,
        "version": doc.version, "error": doc.error or "",
        "data": jloads(doc.data, {}), "gates": jloads(doc.gates, {}),
        "updated_at": doc.updated_at.strftime("%Y-%m-%d %H:%M:%S") if doc.updated_at else "",
    }


def set_json_path(obj, path: str, value):
    """按 `a.b[0].c` 写 JSON 路径，返回旧值。"""
    if not path:
        raise ValueError("缺少 path")
    tokens = re.findall(r"[^.\[\]]+", path)
    cur = obj
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        key = int(tok) if tok.isdigit() else tok
        if last:
            if isinstance(cur, list):
                old = cur[key] if key < len(cur) else None
                if key < len(cur):
                    cur[key] = value
                else:
                    raise ValueError("路径越界")
                return old
            if not isinstance(cur, dict):
                raise ValueError("路径不是对象")
            old = cur.get(key)
            cur[key] = value
            return old
        nxt = cur[key] if isinstance(cur, list) else cur.get(key)
        if nxt is None:
            nxt = {} if not tokens[i + 1].isdigit() else []
            if isinstance(cur, list):
                cur[key] = nxt
            else:
                cur[key] = nxt
        cur = nxt
    return None


def get_json_path(obj, path: str):
    tokens = re.findall(r"[^.\[\]]+", path or "")
    cur = obj
    for tok in tokens:
        if cur is None:
            return None
        if isinstance(cur, list):
            idx = int(tok) if tok.isdigit() else None
            if idx is None or idx >= len(cur):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            cur = cur.get(tok)
        else:
            return None
    return cur


# ------------------------------------------------------------------ 资产同步
def _asset_views(project_id: int, asset: HibtvAsset) -> Dict:
    return jloads(asset.views, {})


def sync_assets(db: Session, project: HibtvProject, kind: str, rows: Sequence[Dict]) -> int:
    """把文档里的角色 / 场景 / 道具同步成资产卡（按 code 更新或新建）。"""
    existing = {a.code: a for a in db.query(HibtvAsset).filter(
        HibtvAsset.project_id == project.id, HibtvAsset.kind == kind).all()}
    seen = set()
    count = 0
    prefix = {"cast": "C", "scene": "S", "prop": "P"}.get(kind, "X")
    for i, row in enumerate(rows):
        code = (row.get("id") or row.get("code") or "").strip() or f"{prefix}{i + 1:02d}"
        name = (row.get("name") or code or f"{kind}{i + 1}").strip()
        if not code:
            continue
        seen.add(code)
        asset = existing.get(code)
        if asset is None:
            asset = HibtvAsset(project_id=project.id, kind=kind, code=code, name=name,
                               data=jdumps(row), views="{}", status="empty")
            db.add(asset)
        else:
            asset.name = name
            asset.data = jdumps(row)
        asset.sort = i
        asset.updated_at = now()
        count += 1
    for code, asset in existing.items():
        if code not in seen:
            asset.hidden = True
        elif asset.hidden and not db.query(HibtvChange).filter(
                HibtvChange.project_id == project.id,
                HibtvChange.what == f"asset:{asset.id}",
                HibtvChange.path == "hidden",
                HibtvChange.reverted.is_(False)).first():
            # 文档里又出现这个 code（常见于撤销「从文档删掉这角色」）：
            # 只恢复不是用户亲手隐藏的卡，否则隐藏的卡会被同步反复拉回来。
            asset.hidden = False
    db.flush()
    return count


def asset_payload(project_id: int, asset: HibtvAsset) -> Dict:
    views = jloads(asset.views, {})
    out_views: Dict[str, Dict] = {}
    for view, meta in (views or {}).items():
        entry = dict(meta or {})
        rel = entry.get("path") or ""
        entry["url"] = f"/files/hibtv/{project_id}/{rel}" if rel else ""
        out_views[view] = entry
    return {
        "id": asset.id, "kind": asset.kind, "code": asset.code, "name": asset.name,
        "data": jloads(asset.data, {}), "views": out_views,
        "status": asset.status, "version": asset.version, "hidden": bool(asset.hidden),
        "sort": asset.sort,
        "defs": VIEW_DEFS.get(asset.kind, []),
        "anchor": VIEW_DEFS.get(asset.kind, [["front", "正面"]])[0][0],
    }


# ------------------------------------------------------------------ 作业投递
def enqueue(db: Session, project_id: int, kind: str, *,
            ep: int = 0, ref: str = "", payload: Optional[Dict] = None) -> HibtvJob:
    job = HibtvJob(project_id=project_id, kind=kind, ep=int(ep), ref=ref or "",
                   status="queued", payload=jdumps(payload or {}))
    db.add(job)
    db.flush()
    return job


def job_payload(job: HibtvJob) -> Dict:
    return {
        "id": job.id, "kind": job.kind, "ep": job.ep, "ref": job.ref,
        "status": job.status, "progress": job.progress, "stage": job.stage,
        "error": job.error or "", "payload": jloads(job.payload, {}),
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else "",
        "finished_at": job.finished_at.strftime("%Y-%m-%d %H:%M:%S") if job.finished_at else "",
    }


# ------------------------------------------------------------------ 执行入口
async def run_job(job_id: int) -> None:
    """执行一条作业；异常一律落 failed（不抛给调度循环）。"""
    with SessionLocal() as db:
        job = db.get(HibtvJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = now()
        job.progress = 5
        db.commit()
        project = db.get(HibtvProject, job.project_id)
        if project is None:
            _fail(db, job, "项目不存在")
            return
        try:
            handler = {
                "outline": _job_outline, "cast": _job_cast, "art": _job_art,
                "script": _job_script, "storyboard": _job_storyboard,
                "images": _job_images, "compose": _job_compose,
            }.get(job.kind)
            if handler is None:
                _fail(db, job, f"未知作业类型 {job.kind}")
                return
            await handler(db, project, job)
            job.status = "done"
            job.progress = 100
            job.stage = "完成"
            job.finished_at = now()
            db.commit()
        except Exception as exc:  # noqa: BLE001 —— 作业失败必须收敛
            logger.exception("短剧作业 #%s(%s) 失败", job.id, job.kind)
            db.rollback()
            job = db.get(HibtvJob, job_id)
            if job is not None:
                _fail(db, job, str(exc))
                _refund(db, project, job)
                if job.kind == "images":
                    # 出图中断后不能把资产永远卡在「出图中」
                    fresh = db.get(HibtvProject, job.project_id)
                    if fresh is not None:
                        reset_drawing(db, fresh)


def _fail(db: Session, job: HibtvJob, message: str) -> None:
    job.status = "failed"
    job.error = str(message)[:2000]
    job.finished_at = now()
    db.commit()


def _progress(db: Session, job: HibtvJob, pct: int, stage: str) -> None:
    job.progress = max(0, min(100, int(pct)))
    job.stage = stage
    db.commit()


def _refund(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    """出图 / 拼接作业系统失败时全额退还预扣积分（只退一次）。"""
    payload = jloads(job.payload, {})
    cost = int(payload.get("cost") or 0)
    if cost <= 0 or payload.get("refunded"):
        return
    user = db.get(User, project.user_id)
    if user is not None:
        add_credits(db, user, cost, "refund", note=f"短剧 {job.kind} 作业失败退还",
                    task_id=None)
    payload["refunded"] = True
    job.payload = jdumps(payload)
    db.commit()


def _parsed(db: Session, project: HibtvProject) -> tuple:
    script = hibtv_parse.parse_script(project.script_text or "")
    art = hibtv_parse.parse_art(project.art_text or "")
    return script, art


def _require_outline(db: Session, project: HibtvProject) -> Dict:
    doc = get_doc(db, project.id, "outline")
    if doc is None or doc.status != "done":
        raise RuntimeError("大纲尚未生成，先跑一次「大纲」")
    return jloads(doc.data, {})


def _art_doc(db: Session, project: HibtvProject) -> Optional[Dict]:
    """质量门对账用的是**已落库的美术文档**（scenes / props 带 id）。"""
    doc = get_doc(db, project.id, "art")
    if doc is None or doc.status != "done":
        return None
    return jloads(doc.data, {})


# ------------------------------------------------------------------ 各类作业
async def _job_outline(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    _progress(db, job, 15, "解析剧本与美术设定")
    script, art = _parsed(db, project)
    _progress(db, job, 40, "生成大纲骨架")
    data, source = await hibtv_llm.generate(
        "outline",
        user_text=hibtv_llm.outline_prompt(project, script, art),
        fallback=lambda: hibtv_llm.outline_from_parse(project, script, art),
    )
    hibtv_llm.ensure_reuse_plan(data)
    _progress(db, job, 80, "跑质量门")
    gates = hibtv_gates.run_gates("outline", data)
    save_doc(db, project.id, "outline", 0, data, gates=gates)
    project.status = "ready"
    project.detected = jdumps(hibtv_parse.detect_summary(script, art))
    project.updated_at = now()
    db.commit()
    logger.info("短剧大纲已生成（来源 %s，质量门 %s）", source, gates.get("status"))


async def _job_cast(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    _progress(db, job, 30, "解析美术设定")
    _script, art = _parsed(db, project)
    _progress(db, job, 60, "生成角色卡")
    data, source = await hibtv_llm.generate(
        "cast",
        user_text=hibtv_llm.cast_prompt(project, art, outline),
        fallback=lambda: hibtv_llm.cast_from_parse(project, art, outline),
    )
    hibtv_llm.ensure_cast_ids(data)
    _progress(db, job, 90, "同步资产卡")
    save_doc(db, project.id, "cast", 0, data)
    sync_assets(db, project, "cast", data.get("characters") or [])
    db.commit()
    logger.info("短剧角色卡已生成（来源 %s，%d 人）", source, len(data.get("characters") or []))


async def _job_art(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    _progress(db, job, 30, "解析美术设定")
    _script, art = _parsed(db, project)
    _progress(db, job, 60, "生成场景与道具")
    data, source = await hibtv_llm.generate(
        "art",
        user_text=hibtv_llm.art_prompt(project, art, outline),
        fallback=lambda: hibtv_llm.art_from_parse(project, art, outline),
    )
    _progress(db, job, 90, "同步资产卡")
    save_doc(db, project.id, "art", 0, data)
    sync_assets(db, project, "scene", data.get("scenes") or [])
    sync_assets(db, project, "prop", data.get("props") or [])
    db.commit()
    logger.info("短剧美术设定已生成（来源 %s，%d 场景 / %d 道具）", source,
                len(data.get("scenes") or []), len(data.get("props") or []))


def _episode_of(script: Dict, ep: int) -> Optional[Dict]:
    for row in script.get("episodes") or []:
        if int(row.get("ep") or 0) == int(ep):
            return row
    return None


async def _job_script(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    script_parse, _art_parse = _parsed(db, project)
    ep_row = _episode_of(script_parse, job.ep) or (script_parse["episodes"][0] if script_parse["episodes"] else None)
    if ep_row is None:
        raise RuntimeError("剧本里没有这一集")
    _progress(db, job, 40, f"结构化第 {job.ep} 集")
    prev = get_doc(db, project.id, "script", job.ep)
    hint = hibtv_gates.hint_of(jloads(prev.gates, {}) if prev else None)
    data, source = await hibtv_llm.generate(
        "script",
        user_text=hibtv_llm.script_prompt(project, ep_row, outline),
        fallback=lambda: hibtv_llm.script_from_parse(project, ep_row, outline),
        gates_hint=hint,
    )
    hibtv_llm.clamp_target_seconds(data, script_parse, job.ep)
    _progress(db, job, 80, "跑质量门")
    gates = hibtv_gates.run_gates("script", data, outline=outline, art=_art_doc(db, project))
    save_doc(db, project.id, "script", job.ep, data, gates=gates)
    db.commit()
    logger.info("短剧剧本 E%02d 已生成（来源 %s，质量门 %s）", job.ep, source, gates.get("status"))


async def _job_storyboard(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    script_doc = get_doc(db, project.id, "script", job.ep)
    if script_doc is None or script_doc.status != "done":
        # 先补跑剧本，保证分镜有事实来源
        await _job_script(db, project, job)
        script_doc = get_doc(db, project.id, "script", job.ep)
    if script_doc is None:
        raise RuntimeError("本集剧本生成失败，无法出分镜")
    script_data = jloads(script_doc.data, {})
    _progress(db, job, 45, f"第 {job.ep} 集分段切镜")
    data, source = await hibtv_llm.generate(
        "storyboard",
        user_text=hibtv_llm.storyboard_prompt(project, script_data, outline),
        fallback=lambda: hibtv_llm.storyboard_from_script(project, script_data, outline),
    )
    script_ep = next((e for e in script_data.get("episodes") or []
                      if int(e.get("ep") or 0) == int(job.ep)),
                     (script_data.get("episodes") or [{}])[0])
    # 生成侧先把「台词装不下 / 越场道具 / 段超上限」这三类必挂项消化掉，再跑门
    hibtv_llm.fit_dialogue_seconds(data, script_ep)
    hibtv_llm.prune_foreign_props(data, script_ep)
    added = hibtv_llm.split_oversized(data, settings.hibtv_seg_max_seconds, job.ep)
    fixed = hibtv_llm.ensure_segment_prompts(data, script_ep, outline)
    if added or fixed:
        logger.info("短剧分镜 E%02d 拆段 +%d、补齐提示词 %d 段", job.ep, added, fixed)
    _progress(db, job, 80, "跑质量门")
    gates = hibtv_gates.run_gates("storyboard", data, outline=outline,
                                  art=_art_doc(db, project), script=script_data)
    save_doc(db, project.id, "storyboard", job.ep, data, gates=gates)
    sync_segments(db, project, job.ep, data)
    db.commit()
    logger.info("短剧分镜 E%02d 已生成（来源 %s，质量门 %s）", job.ep, source, gates.get("status"))


def sync_segments(db: Session, project: HibtvProject, ep: int, storyboard: Dict) -> int:
    """把分镜文档的段清单落成 `hibtv_segments` 行（分镜作业与就地改镜头都走这里）。"""
    rows: List[Dict] = []
    for ep_doc in storyboard.get("episodes") or []:
        if int(ep_doc.get("ep") or 0) == int(ep):
            rows = ep_doc.get("segments") or []
            break
    existing = {s.seg_id: s for s in db.query(HibtvSegment).filter(
        HibtvSegment.project_id == project.id, HibtvSegment.ep == int(ep)).all()}
    count = 0
    for i, seg in enumerate(rows):
        seg_id = str(seg.get("id") or "").strip()
        if not seg_id:
            continue
        obj = existing.get(seg_id)
        if obj is None:
            obj = HibtvSegment(project_id=project.id, ep=int(ep), seg_id=seg_id, idx=i,
                               status="none", versions="[]")
            db.add(obj)
        obj.data = jdumps(seg)
        obj.idx = i
        obj.updated_at = now()
        count += 1
    # 文档里已经没有的段：只清理从未出过片的孤儿行，避免旧段残留到段页/合成清单
    keep = {str(seg.get("id") or "").strip() for seg in rows}
    for seg_id, obj in existing.items():
        if seg_id in keep:
            continue
        if obj.status != "none" or (jloads(obj.versions, []) or []):
            continue
        db.delete(obj)
    db.flush()
    return count


# ------------------------------------------------------------------ 出图作业
def _aspect_canvas(project: HibtvProject) -> tuple:
    if (project.aspect_ratio or "").startswith("16:9"):
        return (int(settings.hibtv_image_height), int(settings.hibtv_image_width))
    return (int(settings.hibtv_image_width), int(settings.hibtv_image_height))


def _anchor_prompt(asset: HibtvAsset) -> str:
    data = jloads(asset.data, {})
    image = data.get("image") or {}
    prompt = (image.get("prompt") or data.get("prompt") or "").strip()
    if not prompt:
        prompt = f"{asset.name}，{data.get('summary') or ''}，当代中国都市写实风格。"
    return prompt


def _view_prompt(asset: HibtvAsset, view: str) -> str:
    """视图提示词：锚点正文 + 视角补写 + 用户「改图」补充说明。"""
    defs = VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    anchor = defs[0][0]
    base = _anchor_prompt(asset)
    data = jloads(asset.data, {})
    hint = ((data.get("hints") or {}).get(view) or "").strip()
    if not hint and view != anchor:
        hint = VIEW_HINT.get(view, "")
    if not hint:
        return base
    tail = ("以参考图为准，人物与空间的所有细节保持一致。" if view == anchor
            else "以参考图为准，人物/空间的所有细节保持一致，只改变视角。")
    return f"{base}；{hint}。{tail}"


def _register_upload(db: Session, user_id: int, path: Path) -> Optional[int]:
    """把出图产物登记成 Upload（可供 Task.ref_image_ids 直接复用）。"""
    try:
        up = Upload(user_id=user_id, slot="reference", filename=path.name,
                    path=str(path), md5="", size=path.stat().st_size if path.exists() else 0)
        db.add(up)
        db.flush()
        return up.id
    except Exception:  # noqa: BLE001
        logger.warning("短剧产物登记 Upload 失败（不影响出图）", exc_info=True)
        return None


async def _draw_view(db: Session, project: HibtvProject, asset: HibtvAsset, view: str,
                     *, ref_view: Optional[str] = None) -> Dict:
    views = jloads(asset.views, {})
    defs = VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    anchor = defs[0][0]
    width, height = _aspect_canvas(project)
    out_rel = f"assets/{asset.kind}/{asset.code}/{view}.png"
    out_abs = project_dir(project.id) / out_rel
    ref_rel = ""
    if view != anchor:
        ref_rel = (views.get(ref_view or anchor) or {}).get("path") or ""
    ref_abs = abs_path(project.id, ref_rel) if ref_rel else None
    if ref_abs is not None and not ref_abs.exists():
        ref_abs = None
    prompt = _view_prompt(asset, view)
    result = await hibtv_image.generate_image(
        prompt=prompt, out_path=out_abs, ref_path=ref_abs, width=width, height=height)
    upload_id = None
    if out_abs.exists():
        upload_id = _register_upload(db, project.user_id, out_abs)
    version = int((views.get(view) or {}).get("version") or 0) + 1
    views[view] = {
        "path": out_rel, "upload_id": upload_id, "version": version,
        "uploaded": False, "stale": False,
        "at": now().strftime("%Y-%m-%d %H:%M"),
    }
    asset.views = jdumps(views)
    asset.version = max(int(asset.version or 0), version)
    asset.updated_at = now()
    return views[view]


def mark_derived_stale(asset: HibtvAsset) -> None:
    views = jloads(asset.views, {})
    defs = VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    anchor = defs[0][0]
    for view, meta in views.items():
        if view != anchor and meta and meta.get("path"):
            meta["stale"] = True
    asset.views = jdumps(views)


def _asset_query(db: Session, project: HibtvProject, payload: Dict):
    kind = str(payload.get("kind") or "")
    codes = payload.get("codes") or ([payload.get("code")] if payload.get("code") else [])
    asset_id = payload.get("asset_id")
    query = db.query(HibtvAsset).filter(HibtvAsset.project_id == project.id)
    if kind:
        query = query.filter(HibtvAsset.kind == kind)
    if asset_id:
        query = query.filter(HibtvAsset.id == int(asset_id))
    if codes:
        query = query.filter(HibtvAsset.code.in_(list(codes)))
    return query.order_by(HibtvAsset.kind, HibtvAsset.sort)


def image_steps(db: Session, project: HibtvProject, payload: Dict) -> int:
    """预估一次出图作业的图片张数（用于提交时一次性预扣积分）。"""
    assets = _asset_query(db, project, payload).all()
    if not assets:
        return 0
    mode = str(payload.get("mode") or "anchor")
    steps = 0
    for asset in assets:
        views = jloads(asset.views, {})
        defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
        if mode == "anchor":
            steps += 1
        elif mode == "all":
            steps += len([v for v in defs
                          if not ((views.get(v) or {}).get("path")
                                  and not (views.get(v) or {}).get("stale"))])
        else:
            steps += len(payload.get("views") or defs[1:])
    return max(0, steps)


async def _job_images(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    payload = jloads(job.payload, {})
    mode = str(payload.get("mode") or "anchor")
    assets = _asset_query(db, project, payload).all()
    if not assets:
        raise RuntimeError("没有可出图的资产")

    total_steps = 0
    for asset in assets:
        views = jloads(asset.views, {})
        defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
        if mode == "anchor":
            total_steps += 1
        elif mode == "all":
            total_steps += len([v for v in defs if not ((views.get(v) or {}).get("path") and not (views.get(v) or {}).get("stale"))])
        else:
            total_steps += len(payload.get("views") or defs)
    total_steps = max(1, total_steps)

    done = 0
    for asset in assets:
        defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
        anchor = defs[0]
        views = jloads(asset.views, {})
        targets: List[str]
        if mode == "anchor":
            targets = [anchor]
        elif mode == "views":
            targets = list(payload.get("views") or defs[1:])
        else:  # all
            targets = [v for v in defs
                       if not ((views.get(v) or {}).get("path") and not (views.get(v) or {}).get("stale"))]
            if anchor not in targets:
                targets.insert(0, anchor)
        if not targets:
            continue
        asset.status = "drawing"
        db.commit()
        for view in targets:
            done += 1
            _progress(db, job, int(5 + 90 * done / total_steps),
                      f"{asset.code} {asset.name} · {dict(VIEW_DEFS.get(asset.kind, [])).get(view, view)}")
            await _draw_view(db, project, asset, view)
            if view == anchor:
                mark_derived_stale(asset)
            db.commit()
        all_views = jloads(asset.views, {})
        has_anchor = bool((all_views.get(anchor) or {}).get("path"))
        every = all(bool((all_views.get(v) or {}).get("path")) for v in defs)
        asset.status = "done" if (has_anchor and every) else ("review" if has_anchor else "empty")
        asset.updated_at = now()
        db.commit()


def reconcile_status(asset: HibtvAsset) -> None:
    """按已出的视图重算状态（中断/失败后不留「出图中」）。"""
    defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
    views = jloads(asset.views, {})
    has_anchor = bool((views.get(defs[0]) or {}).get("path"))
    every = all(bool((views.get(v) or {}).get("path")) for v in defs)
    asset.status = "done" if (has_anchor and every) else ("review" if has_anchor else "empty")
    asset.updated_at = now()


def reset_drawing(db: Session, project: HibtvProject) -> None:
    """把停在 drawing 的资产收敛回真实状态（出图作业失败时调）。"""
    rows = (db.query(HibtvAsset)
            .filter(HibtvAsset.project_id == project.id, HibtvAsset.status == "drawing")
            .all())
    for asset in rows:
        reconcile_status(asset)
    if rows:
        db.commit()


# ------------------------------------------------------------------ 拼接作业
def snap_duration(seconds: float) -> int:
    cap = int(settings.hibtv_seg_max_seconds)
    allowed = [d for d in (5, 8, 10, 15) if d <= cap] or [5]
    need = float(seconds or 0)
    for d in allowed:
        if d >= need:
            return d
    return allowed[-1]


async def _job_compose(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    payload = jloads(job.payload, {})
    ep = int(payload.get("ep") or job.ep or 1)
    segs = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id, HibtvSegment.ep == ep)
            .order_by(HibtvSegment.idx).all())
    paths: List[str] = []
    planned: List[float] = []
    for seg in segs:
        versions = jloads(seg.versions, [])
        cur = None
        for v in versions:
            if int(v.get("v") or 0) == int(seg.current_version or 0):
                cur = v
                break
        if cur is None and versions:
            cur = versions[-1]
        if not cur or not cur.get("path"):
            raise RuntimeError(f"{seg.seg_id} 还没有成片段，先出片再拼接")
        paths.append(cur["path"] if Path(cur["path"]).is_absolute()
                     else str(abs_path(project.id, cur["path"])))
        data = jloads(seg.data, {})
        planned.append(round(sum(float(c.get("seconds") or 0) for c in (data.get("cuts") or [])), 2))
    _progress(db, job, 40, f"拼接 {len(paths)} 段")
    prev_max = db.query(HibtvRender).filter(
        HibtvRender.project_id == project.id, HibtvRender.ep == ep).count()
    result = hibtv_compose.render_episode(
        project_id=project.id, ep=ep, segment_paths=paths, planned=planned,
        version=prev_max + 1)
    rel = rel_path(project.id, Path(result["path"]))
    render = HibtvRender(project_id=project.id, ep=ep, path=rel,
                         duration_sec=float(result["duration_sec"]), size=int(result["size"]),
                         cutlist=result["cutlist"], version=result["version"])
    db.add(render)
    db.commit()


# ------------------------------------------------------------------ 段与版本
TASK_DONE = "done"
TASK_DEAD = ("failed", "cancelled")


def segment_payload(project_id: int, seg: HibtvSegment) -> Dict:
    """段的对外视图：含版本表、当前版本、视频 URL 与切镜计划秒数。"""
    data = jloads(seg.data, {})
    versions = jloads(seg.versions, [])
    current = None
    for v in versions:
        if int(v.get("v") or 0) == int(seg.current_version or 0):
            current = v
            break
    if current is None and versions:
        current = versions[-1]
    out_versions: List[Dict] = []
    for v in versions:
        item = dict(v)
        rel = item.get("path") or ""
        item["url"] = f"/files/hibtv/{project_id}/{rel}" if rel else ""
        out_versions.append(item)
    cur_url = ""
    if current is not None:
        rel = current.get("path") or ""
        cur_url = f"/files/hibtv/{project_id}/{rel}" if rel else ""
    cuts = data.get("cuts") or []
    return {
        "id": seg.seg_id, "ep": seg.ep, "idx": seg.idx,
        "status": seg.status, "currentVersion": seg.current_version,
        "versions": out_versions, "url": cur_url,
        "data": data, "h3Prompt": data.get("h3Prompt") or "", "cuts": cuts,
        "planSeconds": round(sum(float(c.get("seconds") or 0) for c in cuts), 2),
    }


def sync_segment_tasks(db: Session, project: HibtvProject, ep: int) -> None:
    """拉取式同步：把 Task 的成片回写进段版本（不新开常驻循环）。"""
    segs = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id, HibtvSegment.ep == int(ep))
            .order_by(HibtvSegment.idx).all())
    changed = False
    for seg in segs:
        versions = jloads(seg.versions, [])
        running = False
        dead = False
        for v in versions:
            if v.get("path"):
                continue
            tid = v.get("task_id")
            if not tid:
                continue
            task = db.get(Task, int(tid))
            if task is None:
                continue
            if task.status == TASK_DONE and task.video_path:
                p = Path(task.video_path)
                suffix = p.suffix or ".mp4"
                rel = collect_media(
                    project.id, f"segments/{seg.seg_id}-v{int(v.get('v') or 0)}{suffix}", p)
                if not rel:
                    v["failed"] = True
                    v["error"] = "成片文件丢失，请重新生成这一段"
                    dead = True
                    changed = True
                    continue
                v["path"] = rel
                v["seconds"] = round(float(task.duration or 0), 2)
                v["at"] = now().strftime("%Y-%m-%d %H:%M")
                v.pop("failed", None)
                v.pop("error", None)
                seg.status = "done"
                if not int(seg.current_version or 0):
                    seg.current_version = int(v.get("v") or 0)
                changed = True
            elif task.status in TASK_DEAD:
                v["failed"] = True
                v["error"] = (task.error or task.status)[:200]
                dead = True
                changed = True
            else:
                running = True
        if not any(v.get("path") for v in versions):
            seg.status = "running" if running else ("failed" if dead else "none")
        else:
            seg.status = "running" if running else "done"
        if changed:
            seg.versions = jdumps(versions)
            seg.updated_at = now()
    if changed:
        db.commit()


def segment_ref_ids(db: Session, project: HibtvProject, seg: HibtvSegment) -> List[int]:
    """段的参考图：场景锚点 + 出场角色锚点 + 道具锚点（≤ 9，各取第一个有图的视图）。"""
    data = jloads(seg.data, {})
    outline_doc = get_doc(db, project.id, "outline")
    outline = jloads(outline_doc.data, {}) if outline_doc else {}
    scenes = outline.get("scenes") or []
    pairs: List[tuple] = []
    si = int(data.get("sceneIndex") or 0)
    if 0 < si <= len(scenes):
        pairs.append(("scene", scenes[si - 1].get("id") or ""))
    codes: List[str] = []
    for cut in data.get("cuts") or []:
        for code in (cut.get("characters") or []):
            if code and code not in codes:
                codes.append(code)
    for code in codes[:6]:
        pairs.append(("cast", code))
    prop_codes: List[str] = []
    for cut in data.get("cuts") or []:
        for code in (cut.get("props") or []):
            if code and code not in prop_codes:
                prop_codes.append(code)
    for code in prop_codes[:3]:
        pairs.append(("prop", code))

    ids: List[int] = []
    for kind, code in pairs:
        if not code or len(ids) >= 9:
            continue
        asset = (db.query(HibtvAsset)
                 .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == kind,
                         HibtvAsset.code == code)
                 .first())
        if asset is None:
            continue
        views = jloads(asset.views, {})
        order = [v[0] for v in VIEW_DEFS.get(kind, [["front"]])]
        for view in order:
            uid = (views.get(view) or {}).get("upload_id")
            if uid and int(uid) not in ids:
                ids.append(int(uid))
                break
    return ids[:9]

"""短剧工作台：作业执行器（worker 常驻循环调用）。

作业种类（`hibtv_jobs.kind`）：
- outline / cast / art / refs / script / staging / storyboard ── codex 网关跑 skill，平台只搬字段
- images ── 资产单图（`payload.mode=assets`）/ 关键帧（`mode=frames`），都走 qwen21 节点
- compose ── 整集 ffmpeg 拼接

职责边界（改这个文件前先读一遍，避免平台又长出手搓）：
- 网关：拼 prompt、跑 skill、跑脚本门、写产物文件；
- 平台：鉴权、提交、轮询、下载落盘、**纯字段搬运**回写 docs、像素生成、界面；
- 平台**不再**：读 skill 规范、拼业务提示词、写确定性骨架、跑 node 门。

所有状态机都落在 `hibtv_jobs` 上，前端 4s 轮询；已产出物不受取消影响（与原型一致）。
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import HIBTV_DIR, settings
from ..database import SessionLocal
from ..models import (HibtvAsset, HibtvChange, HibtvDoc, HibtvFrame, HibtvJob, HibtvProject,
                      HibtvRender, HibtvSegment, Task, Upload, User)
from . import h3_time, hibtv_assets, hibtv_compose, hibtv_codex, hibtv_frames, hibtv_image
from .billing import add_credits
from .hibtv_contracts import (digest, design, segment_input, current_segments, require_document, asset_input, file_hash)

logger = logging.getLogger(__name__)

# 文本 7 段：一律交给 codex 网关（平台不落地任何 prompt 模板）
TEXT_KINDS = ("outline", "cast", "art", "refs", "script", "staging", "storyboard")
# 全集级文档（ep 恒为 0）与按集文档
DOC_KINDS = ("outline", "cast", "art", "staging", "script", "storyboard")
EP_KINDS = ("script", "staging", "storyboard")


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
             gates: Optional[Dict] = None, status: str = "done", error: str = "",
             meta: Optional[Dict] = None) -> HibtvDoc:
    doc = ensure_doc(db, project_id, kind, ep)
    doc.data = jdumps(data)
    doc.version = int(doc.version or 0) + 1
    doc.status = status
    doc.error = error
    if gates is not None:
        doc.gates = jdumps(gates)
    if meta is not None:
        doc.meta = jdumps(meta)
    doc.updated_at = now()
    # 落盘留档（便于排查 / 直接喂下游阶段）
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
        "meta": jloads(doc.meta, {}),
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
def sync_assets(db: Session, project: HibtvProject, kind: str, rows: Sequence[Dict]) -> int:
    """把文档里的角色 / 场景 / 道具同步成资产卡（按 code 更新或新建）。

    资产卡存 skill 给的那份 JSON：锚点提示词从 `data.image.prompt` 读，锚点主图走
    `main_path`，派生视图走 `views`（同步不覆盖）。用户拥有的字段
    （`imageConfirmed` / `hints` / `refs`）跨同步保留；image.prompt 变了就把派生视图全标过期。
    """
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
            old = jloads(asset.data, {})
            merged = dict(row)
            for key in ("imageConfirmed", "confirmedHash", "hints", "refs", "refsPrompts", "imageTasks"):
                if key in old and key not in merged:
                    merged[key] = old[key]
            old_prompt = str((old.get("image") or {}).get("prompt") or "")
            new_prompt = str((row.get("image") or {}).get("prompt") or "")
            asset.name = name
            asset.data = jdumps(merged)
            if {k:v for k,v in design(old).items() if k not in ("refs", "imageTasks")} != {k:v for k,v in design(merged).items() if k not in ("refs", "imageTasks")}:
                merged["imageConfirmed"] = False
                merged.pop("confirmedHash", None)
                asset.data = jdumps(merged)
                if kind == "cast":
                    # 角色描述变动后，旧 character-refs 提示词同样失效。
                    for key in ("refs", "refsPrompts", "imageTasks"):
                        merged.pop(key, None)
                    asset.data = jdumps(merged)
                # 锚点描述变了 → 全部派生视图过期（只标不删）
                hibtv_assets.mark_stale(asset, "__anchor")
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


def asset_status(asset: HibtvAsset) -> str:
    """资产状态只看锚点主图：没图 = 空（empty）；有图未确认 = 待评审（review）；
    有图已确认 = 完成（done）。派生视图出图不会把已确认的锚点打回 review。"""
    if not (asset.main_path or "").strip():
        return "empty"
    return "done" if jloads(asset.data, {}).get("imageConfirmed") else "review"


def asset_payload(project_id: int, asset: HibtvAsset) -> Dict:
    """资产卡对外视图：锚点主图 + 派生视图（按 VIEW_DEFS 顺序）+ 提示词面板。"""
    data = jloads(asset.data, {})
    main_rel = (asset.main_path or "").strip()
    meta = jloads(asset.main_meta, {})
    image = data.get("image") or {}
    size = list(meta.get("size") or hibtv_assets.size_for(asset.kind))
    raw_views = jloads(asset.views, {}) or {}
    aid = hibtv_assets.anchor_id(asset.kind)
    views: Dict[str, Dict] = {}
    legacy = []
    for spec in hibtv_assets.view_defs(asset.kind, data):
        vid = spec["id"]
        if vid == aid:
            continue          # 锚点走 main，不重复列
        item = raw_views.get(vid) or {}
        rel = str(item.get("path") or "").strip()
        views[vid] = {
            "label": spec["label"], "anchor": False,
            "url": f"/files/hibtv/{project_id}/{rel}" if rel else "",
            "path": rel,
            "uploadId": int(item.get("uploadId") or 0),
            "version": int(item.get("version") or 0),
            "status": item.get("status") or ("review" if rel else "empty"),
            "stale": bool(item.get("stale")),
            "confirmed": bool(item.get("confirmed")),
            "at": str(item.get("at") or ""),
        }
    # 历史项目的旧格式视图（只 path/version）：只读展示，不写回
    for view, item in raw_views.items():
        if view in views or view == aid or view == "__sheet":
            continue
        rel = (item or {}).get("path") or "" if isinstance(item, dict) else ""
        if rel:
            legacy.append({"view": view,
                           "url": f"/files/hibtv/{project_id}/{rel}",
                           "version": (item or {}).get("version") or 0})
    return {
        "id": asset.id, "kind": asset.kind, "code": asset.code, "name": asset.name,
        "data": data,
        "prompt": hibtv_assets.prompt_of(data),
        "negative": hibtv_assets.negative_of(data),
        "promptLocal": str(image.get("promptLocal") or ""),
        "size": size,
        "viewDefs": [{"id": s["id"], "label": s["label"], "anchor": bool(s.get("anchor"))}
                     for s in hibtv_assets.view_defs(asset.kind, data)],
        "anchorId": aid,
        "imageConfirmed": hibtv_assets.main_current(type("Project", (), {"id": project_id})(), asset),
        "main": {
            "path": main_rel,
            "url": f"/files/hibtv/{project_id}/{main_rel}" if main_rel else "",
            "uploadId": int(asset.main_upload_id or 0),
            "version": int(asset.version or 0),
            "meta": meta,
            "at": str(meta.get("at") or ""),
        },
        "views": views,
        "legacyViews": legacy,
        "status": ("stale" if main_rel and asset.status != "drawing" and not
                   hibtv_assets.main_current(type("Project", (), {"id": project_id})(), asset, confirmed=False)
                   else asset.status or asset_status(asset)),
        "version": int(asset.version or 0),
        "hidden": bool(asset.hidden), "sort": asset.sort,
    }


# ------------------------------------------------------------------ 作业投递
def enqueue(db: Session, project_id: int, kind: str, *,
            ep: int = 0, ref: str = "", payload: Optional[Dict] = None) -> HibtvJob:
    job = HibtvJob(project_id=project_id, kind=kind, ep=int(ep), ref=ref or "",
                   status="queued", payload=jdumps(payload or {}))
    db.add(job)
    db.flush()
    return job


def ensure_refs(db: Session, project_id: int, after_job: int = 0) -> Optional[HibtvJob]:
    """cast 成功后自动接一段 refs：同一项目不重复排队，重跑 cast 才会重排。"""
    latest = (db.query(HibtvJob)
              .filter(HibtvJob.project_id == project_id, HibtvJob.kind == "refs")
              .order_by(HibtvJob.id.desc()).first())
    if (latest is not None and latest.id > int(after_job or 0)
            and latest.status not in ("failed", "cancelled")):
        return None
    job = enqueue(db, project_id, "refs", payload={"auto": "cast"})
    db.commit()
    return job


def job_payload(job: HibtvJob) -> Dict:
    usage = jloads(job.usage, {})
    artifacts = jloads(job.artifacts, [])
    return {
        "id": job.id, "kind": job.kind, "ep": job.ep, "ref": job.ref,
        "status": job.status, "progress": job.progress, "stage": job.stage,
        "error": job.error or "", "payload": jloads(job.payload, {}),
        "codexJobId": job.codex_job_id or "",
        "artifacts": artifacts if isinstance(artifacts, list) else [],
        "usage": usage,
        "tokens": {
            "in": int(usage.get("input_tokens") or 0),
            "cached": int(usage.get("cached_input_tokens") or 0),
            "out": int(usage.get("output_tokens") or 0),
        },
        "elapsedSec": float(usage.get("elapsed_sec") or 0),
        "queuePosition": usage.get("queue_position"),
        "heartbeatAt": usage.get("heartbeat_at") or "",
        "timeoutKind": usage.get("timeout_kind") or "",
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
            if job.kind in TEXT_KINDS:
                # 文本归 codex：提交 → 轮询 → 拉产物 → 搬字段，全在 hibtv_codex
                await hibtv_codex.run(db, project, job)
            else:
                handler = {"images": _job_images, "compose": _job_compose}.get(job.kind)
                if handler is None:
                    _fail(db, job, f"未知作业类型 {job.kind}")
                    return
                await handler(db, project, job)
            # 中途被人工取消的作业保留 cancelled，不被收尾覆盖成 done
            db.refresh(job)
            if job.status != "cancelled":
                job.status = "done"
                job.progress = 100
                job.stage = "完成"
                job.finished_at = now()
            db.commit()
            if job.kind == "cast" and job.status == "done":
                # 参考图（refs）本来就在 TEXT_KINDS 里，只是没人触发：cast 成功即自动接力
                try:
                    ensure_refs(db, project.id, after_job=job.id)
                except Exception:  # noqa: BLE001 —— 接力失败不影响 cast 本身
                    logger.exception("cast 完成后自动排队 refs 失败")
                    db.rollback()
        except Exception as exc:  # noqa: BLE001 —— 作业失败必须收敛
            logger.exception("短剧作业 #%s(%s) 失败", job.id, job.kind)
            db.rollback()
            job = db.get(HibtvJob, job_id)
            if job is not None:
                _fail(db, job, str(exc))
                _refund(db, project, job)
                if job.kind == "images":
                    # 出图中断后不能把资产 / 帧永远卡在「出图中」
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


def set_stage(db: Session, job: HibtvJob, pct: int, stage: str) -> None:
    """codex 编排层用的进度写入口（与 `_progress` 同义，对外公开）。"""
    _progress(db, job, pct, stage)


def save_artifacts(db: Session, job: HibtvJob, rows: List[Dict]) -> None:
    """产物索引落库（path / abs / bytes），供前端「全部产物」抽屉与下游引用。"""
    job.artifacts = jdumps([{
        "path": str(r.get("path") or ""), "abs": str(r.get("abs") or ""),
        "bytes": int(r.get("bytes") or 0),
    } for r in (rows or []) if isinstance(r, dict)])
    db.commit()


def save_usage(db: Session, job: HibtvJob, usage: Dict) -> None:
    """tokens / 耗时 / attempts / timeout_kind 落库（一集 7.2M token 必须看得见）。"""
    job.usage = jdumps(usage or {})
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


def cancel_running(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    """取消运行中的作业：退款 + 复位出图态（已产出物保留）。调用方负责 commit。"""
    job.status = "cancelled"
    job.stage = "已取消"
    job.finished_at = now()
    _refund(db, project, job)
    if job.kind == "images":
        reset_drawing(db, project)


# 僵尸作业判定阈值（秒）：进程被杀 / 节点失联时 running 不会自己收敛，
# 而串行闸门只看 status=running，不回收就会把后续出图 / 拼接永久堵死。
STALE_RUNNING_SECONDS = {"images": 1800, "compose": 3600}
DEFAULT_STALE_RUNNING_SECONDS = 7200     # 文本段走网关，单 job 上限 1800s，留足余量


def _is_stale_running(job: HibtvJob, ref: Optional[datetime] = None) -> bool:
    limit = STALE_RUNNING_SECONDS.get(job.kind, DEFAULT_STALE_RUNNING_SECONDS)
    start = job.started_at or job.created_at
    if start is None:
        return False
    return (ref or datetime.now()) - start > timedelta(seconds=limit)


def recover_running_jobs(db: Optional[Session] = None, *, only_stale: bool = True) -> int:
    """把僵尸 running 作业收敛为 failed：退款 + 复位出图态，返回回收条数。

    - 启动时（only_stale=False）：进程内还没有任何执行协程，running 必是孤儿；
    - 运行中（only_stale=True）：只收敛 started_at 超时的，避免误伤在跑的作业。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        rows = db.query(HibtvJob).filter(HibtvJob.status == "running").all()
        ref = datetime.now()
        n = 0
        for job in rows:
            if only_stale and not _is_stale_running(job, ref):
                continue
            project = db.get(HibtvProject, job.project_id)
            _fail(db, job, "作业中断（服务重启或执行协程丢失），已自动回收")
            if project is not None:
                _refund(db, project, job)
                if job.kind == "images":
                    reset_drawing(db, project)
            logger.warning("回收僵尸短剧作业 #%s(%s)", job.id, job.kind)
            n += 1
        return n
    finally:
        if own:
            db.close()


# ------------------------------------------------------------------ 段同步
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
        changed = digest(segment_input(jloads(obj.data, {}))) != digest(segment_input(seg))
        seg["active"] = True
        obj.data = jdumps(seg)
        if changed:
            obj.current_version = 0
            obj.status = "none"
        obj.idx = i
        obj.updated_at = now()
        count += 1
    # 文档里已经没有的段：只清理从未出过片的孤儿行，避免旧段残留到段页/合成清单
    keep = {str(seg.get("id") or "").strip() for seg in rows}
    for seg_id, obj in existing.items():
        if seg_id in keep:
            continue
        data = jloads(obj.data, {})
        data["active"] = False
        obj.data = jdumps(data)
        obj.status = "archived"
    db.flush()
    return count


# ------------------------------------------------------------------ 出图作业
def register_upload(db: Session, user_id: int, path: Path) -> Optional[int]:
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


def _asset_query(db: Session, project: HibtvProject, payload: Dict):
    kind = str(payload.get("kind") or "")
    codes = payload.get("codes") or ([payload.get("code")] if payload.get("code") else [])
    asset_id = payload.get("asset_id")
    query = db.query(HibtvAsset).filter(HibtvAsset.project_id == project.id, HibtvAsset.hidden.is_(False))
    if kind:
        query = query.filter(HibtvAsset.kind == kind)
    if asset_id:
        query = query.filter(HibtvAsset.id == int(asset_id))
    if codes:
        query = query.filter(HibtvAsset.code.in_(list(codes)))
    return query.order_by(HibtvAsset.kind, HibtvAsset.sort)


def _frame_query(db: Session, project: HibtvProject, payload: Dict):
    query = db.query(HibtvFrame).filter(HibtvFrame.project_id == project.id, HibtvFrame.status != "archived")
    ids = payload.get("frame_ids") or []
    if ids:
        query = query.filter(HibtvFrame.id.in_([int(x) for x in ids]))
    seg_id = str(payload.get("seg_id") or "").strip()
    if seg_id:
        query = query.filter(HibtvFrame.seg_id == seg_id)
    ep = int(payload.get("ep") or 0)
    if ep:
        query = query.filter(HibtvFrame.ep == ep)
    return query.order_by(HibtvFrame.ep, HibtvFrame.seg_id, HibtvFrame.cut_index)


def image_steps(db: Session, project: HibtvProject, payload: Dict) -> int:
    """预估一次出图作业的张数（用于提交时一次性预扣积分）。

    只有两种模式：`assets`（一资产一张单图）与 `frames`（每切一张关键帧）。
    """
    if str(payload.get("mode") or "assets") == "frames":
        return len(_frame_query(db, project, payload).all())
    return len(_asset_query(db, project, payload).all())


async def _job_images(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    payload = jloads(job.payload, {})
    if str(payload.get("mode") or "assets") == "frames":
        await _draw_frames(db, project, job, payload)
    else:
        await _draw_assets(db, project, job, payload)


async def _draw_assets(db: Session, project: HibtvProject, job: HibtvJob,
                       payload: Dict) -> None:
    assets = _asset_query(db, project, payload).all()
    if not assets:
        raise RuntimeError("没有可出图的资产")
    view = str(payload.get("view") or "").strip()
    total = max(1, len(assets))
    for i, asset in enumerate(assets, start=1):
        asset.status = "drawing"
        db.commit()
        label = f"{view} 视图" if view else "锚点图"
        _progress(db, job, int(5 + 90 * i / total), f"{asset.code} {asset.name} · {label}")
        if view:
            await hibtv_assets.draw_view(db, project, asset, view)
        else:
            await hibtv_assets.draw(db, project, asset)
        asset.status = asset_status(asset)
        asset.updated_at = now()
        db.commit()


async def _draw_frames(db: Session, project: HibtvProject, job: HibtvJob,
                       payload: Dict) -> None:
    frames = _frame_query(db, project, payload).all()
    if not frames:
        raise RuntimeError("没有可出图的关键帧（先跑分镜，或勾选要出的帧）")
    total = max(1, len(frames))
    for i, frame in enumerate(frames, start=1):
        frame.status = "drawing"
        db.commit()
        _progress(db, job, int(5 + 90 * i / total),
                  f"{frame.seg_id} · f{int(frame.cut_index)} 关键帧")
        await hibtv_frames.draw(db, project, frame)
        frame.status = "review" if (frame.path or "").strip() else "empty"
        frame.updated_at = now()
        db.commit()


def reset_drawing(db: Session, project: HibtvProject) -> None:
    """把停在 drawing 的资产 / 帧收敛回真实状态（出图作业失败时调）。"""
    rows = (db.query(HibtvAsset)
            .filter(HibtvAsset.project_id == project.id, HibtvAsset.status == "drawing")
            .all())
    for asset in rows:
        asset.status = asset_status(asset)
    frames = (db.query(HibtvFrame)
              .filter(HibtvFrame.project_id == project.id, HibtvFrame.status == "drawing")
              .all())
    for frame in frames:
        frame.status = "review" if (frame.path or "").strip() else "empty"
    if rows or frames:
        db.commit()


# ------------------------------------------------------------------ 拼接作业
def snap_duration(seconds: float) -> float:
    """把期望秒数折成 H3 真实可出的秒数（5+17n 帧 / 24fps，且**不超过**目标）。

    13s 上限 → 311 帧 = 12.9583s；直接传 13 会被模型向上吸附到 328 帧 = 13.67s（超限）。
    """
    return h3_time.seconds_for(seconds)


async def _job_compose(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    payload = jloads(job.payload, {})
    ep = int(payload.get("ep") or job.ep or 1)
    expected = current_segments(db, project, ep, checked=True)
    by_id = {row.seg_id: row for row in db.query(HibtvSegment).filter(
        HibtvSegment.project_id == project.id, HibtvSegment.ep == ep).all()}
    segs = []
    for row in expected:
        seg = by_id.get(row["id"])
        if seg is None or digest(segment_input(row)) != digest(segment_input(jloads(seg.data, {}))):
            raise RuntimeError(f"{row['id']} 当前分镜与段表不一致，请重新导入")
        segs.append(seg)
    paths: List[str] = []
    planned: List[float] = []
    for seg in segs:
        if jloads(seg.data, {}).get("active") is False:
            continue
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
        if cur.get("inputHash") != hibtv_frames.video_input_hash(db, project, seg):
            raise RuntimeError(f"{seg.seg_id} 所选视频依赖旧输入，请重新生成或选择当前版本")
        video_path = Path(cur["path"]) if Path(cur["path"]).is_absolute() else abs_path(project.id, cur["path"])
        if not video_path.is_file() or cur.get("sha256") != file_hash(video_path):
            raise RuntimeError(f"{seg.seg_id} 视频文件缺失或与登记版本不符，请重新生成")
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
        "promptSource": data.get("promptSource") or "",
        "promptSha8": data.get("promptSha8") or "",
        "frameCount": int(data.get("frameCount") or len(cuts)),
        "pictureCount": int(data.get("pictureCount") or len(cuts)),
        "manifestSeconds": float(data.get("manifestSeconds") or 0),
        "oversize": bool(data.get("oversize")),
        "timingMismatch": bool(data.get("timingMismatch")),
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
                v["sha256"] = file_hash(abs_path(project.id, rel))
                v["seconds"] = round(float(task.frames or 0) / 24 if task.frames else float(task.duration or 0), 6)
                v["at"] = now().strftime("%Y-%m-%d %H:%M")
                v.pop("failed", None)
                v.pop("error", None)
                seg.status = "done"
                if (not int(seg.current_version or 0) and
                        v.get("segmentHash") == digest(segment_input(jloads(seg.data, {})))):
                    seg.current_version = int(v.get("v") or 0)
                changed = True
            elif task.status in TASK_DEAD:
                v["failed"] = True
                v["error"] = (task.error or task.status)[:200]
                dead = True
                changed = True
            else:
                running = True
        previous_state = (seg.status, seg.current_version)
        try:
            active_hash = hibtv_frames.video_input_hash(db, project, seg)
        except RuntimeError:
            active_hash = ""
        current = [v for v in versions if v.get("path") and active_hash and v.get("inputHash") == active_hash]
        if current:
            if not any(int(v.get("v") or 0) == int(seg.current_version or 0) for v in current):
                seg.current_version = int(current[-1]["v"])
            seg.status = "running" if running else "done"
        else:
            seg.current_version = 0
            seg.status = "running" if running else ("stale" if any(v.get("path") for v in versions) else ("failed" if dead else "none"))
        changed = changed or previous_state != (seg.status, seg.current_version)
        if changed:
            seg.versions = jdumps(versions)
            seg.updated_at = now()
    if changed:
        db.commit()


def segment_ref_plan(db: Session, project: HibtvProject, seg: HibtvSegment) -> List[Dict]:
    """段的镜头/可选分镜图记录；路线 A 的真实参考清单由 hibtv_frames 汇总。"""
    return hibtv_frames.plan(db, project, seg.seg_id, ep=seg.ep)


def segment_ref_ids(db: Session, project: HibtvProject, seg: HibtvSegment) -> List[int]:
    """路线 A 的段级参考图 id。"""
    ids, _source = hibtv_frames.segment_video_refs(db, project, seg)
    return ids


def video_prompt(prompt: str, _plan: Optional[List[dict]] = None) -> str:
    """段出片提示词：**原样直出**。

    `prompt.md` 本来就是 Ref2VA 提示词（Picture 1 钉 0.00 秒、Picture 2 钉 2.50 秒…），
    而挂的参考图又是同一批关键帧——改写只会把「切点↔图」的契约拆坏，所以这里不做任何加工。
    """
    return (prompt or "").strip()

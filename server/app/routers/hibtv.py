"""短剧工作台 API（`/api/hibtv`），入口是前端的暗金工作台 `/hibtv/*`。

链路：小说 → 大纲 → 角色 → 美术 → 参考图 → 剧本 → 站位 → 分镜 → 镜头素材 → 段出片 → 整集拼接。

约定（这一层只做鉴权、调度、字段转发，**不拼提示词、不解析产物**）：
- 文本 7 段一律交给 codex 网关（`services/hibtv_codex.py`），产物落到
  `DATA_DIR/hibtv/{id}/codex/{gateway_jid}/`，可用 `/files` 与 `artifacts` 接口取回；
- 文档（outline / cast / art / script / staging / storyboard）原样保存 skill 的 JSON；
- 像素阶段：资产单图 + 可选分镜图 + 段级素材 ref2v + 整集拼接；
- 就地编辑、上传替换都写 `hibtv_changes` 底账，并在就地改稿后把门标成「未复跑」。
"""
import json
import shutil
from uuid import uuid4
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import (
    HibtvAsset,
    HibtvChange,
    HibtvDoc,
    HibtvFrame,
    HibtvJob,
    HibtvProject,
    HibtvRender,
    HibtvSegment,
    Task,
    Upload,
    User,
)
from ..schemas import (
    HibtvAssetPatch,
    HibtvDocPatch,
    HibtvFrameImageIn,
    HibtvFrameReferencesIn,
    HibtvImageIn,
    HibtvJobIn,
    HibtvProjectPatch,
    HibtvSegmentChooseIn,
    HibtvSegmentGenerateIn,
)
from ..services import (
    codex_gateway,
    h3_time,
    hibtv_assets,
    hibtv_codex,
    hibtv_compose,
    hibtv_frames,
    hibtv_gates,
    hibtv_image,
    hibtv_jobs,
)
from ..services.billing import add_credits, compute_ad_image_cost, compute_cost
from ..services.hibtv_contracts import (digest, file_hash, asset_input, current_segments,
                                        segment_input, require_document)

router = APIRouter(prefix="/api/hibtv", tags=["hibtv"])

DOC_KINDS = hibtv_jobs.DOC_KINDS          # outline/cast/art/staging/script/storyboard
EP_KINDS = hibtv_jobs.EP_KINDS            # script/staging/storyboard（按集）
TEXT_EXTS = {".md", ".markdown", ".txt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024


# ------------------------------------------------------------------ 通用
def _project(db: Session, project_id: int, user: User) -> HibtvProject:
    project = db.get(HibtvProject, project_id)
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    return project


def _doc_ep(kind: str, ep: int) -> int:
    if kind in EP_KINDS:
        return max(1, int(ep or 1))
    return 0


def _doc_or_404(db: Session, project: HibtvProject, kind: str, ep: int) -> HibtvDoc:
    doc = hibtv_jobs.get_doc(db, project.id, kind, ep)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档尚未生成")
    return doc


def _log_change(db: Session, project_id: int, what: str, path: str,
                old, new, who: str = "你") -> None:
    db.add(HibtvChange(
        project_id=project_id, who=who, what=what, path=path,
        from_value=json.dumps(old, ensure_ascii=False),
        to_value=json.dumps(new, ensure_ascii=False),
    ))


def _doc_key(doc: HibtvDoc) -> str:
    return f"{doc.kind}#{doc.ep}"


def _project_payload(db: Session, project: HibtvProject) -> Dict:
    docs = (db.query(HibtvDoc)
            .filter(HibtvDoc.project_id == project.id)
            .order_by(HibtvDoc.kind, HibtvDoc.ep).all())
    assets = (db.query(HibtvAsset)
              .filter(HibtvAsset.project_id == project.id, HibtvAsset.hidden.is_(False))
              .all())
    segs = db.query(HibtvSegment).filter(HibtvSegment.project_id == project.id).all()
    frames = db.query(HibtvFrame).filter(HibtvFrame.project_id == project.id).all()
    segs = [s for s in segs if hibtv_jobs.jloads(s.data, {}).get("active") is not False]
    frames = [f for f in frames if hibtv_jobs.jloads(f.meta, {}).get("active") is not False]
    asset_count = {"cast": 0, "scene": 0, "prop": 0}
    for asset in assets:
        asset_count[asset.kind] = asset_count.get(asset.kind, 0) + 1
    return {
        "id": project.id,
        "name": project.name,
        "genre": project.genre or "",
        "episodes": int(project.episodes or 1),
        "minutesMin": int(project.minutes_min or 2),
        "minutesMax": int(project.minutes_max or 5),
        "targetSeconds": hibtv_codex.target_seconds(db, project),
        "aspectRatio": project.aspect_ratio or "9:16",
        "style": project.style or "写实",
        "novelName": project.novel_name or "",
        "hasNovel": bool((project.novel_text or "").strip() or (project.novel_path or "").strip()),
        "scriptName": hibtv_jobs.jloads(project.detected, {}).get("sources", {}).get("script", {}).get("name", ""),
        "artName": hibtv_jobs.jloads(project.detected, {}).get("sources", {}).get("art", {}).get("name", ""),
        "hasSources": hibtv_jobs.jloads(project.detected, {}).get("inputMode") == "script-art",
        "keeps": project.keeps or "",
        "detected": hibtv_jobs.jloads(project.detected, {}),
        "status": project.status,
        "createdAt": project.created_at.strftime("%Y-%m-%d %H:%M") if project.created_at else "",
        "updatedAt": project.updated_at.strftime("%Y-%m-%d %H:%M") if project.updated_at else "",
        "docs": {
            _doc_key(d): {
                "status": d.status, "version": d.version,
                "gates": hibtv_jobs.jloads(d.gates, {}),
                "meta": hibtv_jobs.jloads(d.meta, {}),
                "error": d.error or "",
            }
            for d in docs
        },
        "assetCount": asset_count,
        "assetMain": len([a for a in assets if (a.main_path or "").strip()]),
        "segmentCount": {
            "total": len(segs),
            "done": len([s for s in segs if s.status == "done"]),
        },
        "frameCount": {
            "total": len(frames),
            "done": sum(hibtv_frames.frame_current(db, project, f) for f in frames),
        },
    }


def _read_text(file: Optional[UploadFile], label: str) -> tuple:
    """读上传的文本稿（.md/.txt），返回 (文件名, 正文)。"""
    if file is None:
        return "", ""
    name = file.filename or ""
    suffix = Path(name).suffix.lower()
    if suffix not in TEXT_EXTS and suffix != ".docx":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}支持 .md / .txt / .docx 文件")
    content = file.file.read() if hasattr(file.file, "read") else b""
    if len(content) > MAX_TEXT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}不能超过 8MB")
    if suffix == ".docx":
        import io
        import zipfile
        import xml.etree.ElementTree as ET
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > MAX_TEXT_BYTES:
                    raise ValueError("解压后正文过大")
                root = ET.fromstring(archive.read(info))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return name, "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))
        except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}不是有效的 Word 文档") from exc
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return name, content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}编码无法识别，请另存为 UTF-8")


def _resync(db: Session, project: HibtvProject, kind: str, ep: int = 0) -> None:
    """就地编辑后把文档变更同步进资产卡 / 段表。"""
    doc = hibtv_jobs.get_doc(db, project.id, kind, ep)
    if doc is None:
        return
    data = hibtv_jobs.jloads(doc.data, {})
    if kind == "cast":
        hibtv_jobs.sync_assets(db, project, "cast", data.get("characters") or [])
    elif kind == "art":
        hibtv_jobs.sync_assets(db, project, "scene", data.get("scenes") or [])
        hibtv_jobs.sync_assets(db, project, "prop", data.get("props") or [])
    elif kind == "storyboard":
        # 改镜头（秒数/描述/景别）必须落到段表，否则段页与出片时长取到的是旧值
        hibtv_jobs.sync_segments(db, project, ep, data)
        from ..services.hibtv_stages import sync_frames
        segments = next((e.get("segments", []) for e in data.get("episodes", []) if int(e.get("ep") or 0) == ep), [])
        sync_frames(db, project, ep, {}, segments)


def _mark_stale(db: Session, doc: HibtvDoc, why: str = "改动后门未复跑") -> None:
    """就地改稿后门结果必然作废：如实标 stale，不伪装成通过。"""
    gates = hibtv_jobs.jloads(doc.gates, {})
    gates.update(hibtv_gates.stale(why))
    doc.gates = hibtv_jobs.jdumps(gates)


# ------------------------------------------------------------------ 状态
@router.get("/status")
def hibtv_status(user: User = Depends(get_current_user)):
    """链路可用性：codex 网关 / 生图节点 / 门 / ffmpeg，前端据此标注降级。"""
    return {
        "enabled": bool(settings.hibtv_enabled),
        "codex": codex_gateway.status(),
        "image": hibtv_image.status(),
        "gates": hibtv_gates.status(),
        "compose": hibtv_compose.status(),
        "segMaxSeconds": float(settings.hibtv_seg_max_seconds),
        "segMaxFrames": h3_time.frames_for(float(settings.hibtv_seg_max_seconds)),
        "frameSize": {
            "9:16": list(hibtv_frames.canvas(_ratio_project("9:16"))),
            "16:9": list(hibtv_frames.canvas(_ratio_project("16:9"))),
        },
        "assetSizes": {k: list(v) for k, v in (settings.hibtv_asset_sizes or {}).items()},
        "promptLang": hibtv_assets.prompt_lang(),
        "cost": {
            "image": int(settings.cost_ad_image or 0),
            "video": {str(d): compute_cost(d, "768p") for d in (5, 8, 10, 15)},
        },
        "credits": int(user.credits or 0),
    }


class _RatioStub:
    """只为复用 `hibtv_frames.canvas()` 的画幅查询（它只读 aspect_ratio）。"""

    def __init__(self, ratio: str):
        self.aspect_ratio = ratio


def _ratio_project(ratio: str) -> _RatioStub:
    return _RatioStub(ratio)


@router.get("/ai/codex/status")
async def codex_status():
    """网关链路自检：掉线直接 503（前端红态并禁用提交）。

    返回体刻意**不含 token**：只给「有没有 token / token 文件路径 / skills 数」。
    """
    base = codex_gateway.status()
    if not base["available"]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, base)
    try:
        health = await codex_gateway.healthz()
        stages = await codex_gateway.skills()
        stats = await codex_gateway.stats()
    except codex_gateway.CodexAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, exc.as_dict()) from exc
    except codex_gateway.CodexError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            {"note": exc.message, **base}) from exc
    return {
        **base,
        "healthz": health,
        "skills": stages,
        "stats": stats,
        "note": "" if stages else "网关没有返回任何 skill（CODEX_HOME/skills 未安装？）",
    }


# ------------------------------------------------------------------ 项目
@router.get("/projects")
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(HibtvProject).order_by(HibtvProject.id.desc())
    if user.role != "admin":
        query = query.filter(HibtvProject.user_id == user.id)
    return [_project_payload(db, p) for p in query.limit(100).all()]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    script: Optional[UploadFile] = File(default=None),
    art: Optional[UploadFile] = File(default=None),
    name: str = Form(default=""),
    genre: str = Form(default=""),
    episodes: int = Form(default=0),
    minutes_min: int = Form(default=2),
    minutes_max: int = Form(default=5),
    ratio: str = Form(default="9:16"),
    style: str = Form(default="写实"),
    keeps: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新建项目：定稿剧本与美术设定均为必填，不接受小说建项。"""
    if not settings.hibtv_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "短剧工作台未启用")
    script_name, script_text = _read_text(script, "定稿剧本")
    art_name, art_text = _read_text(art, "美术设定")
    if not script_text.strip() or not art_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请同时上传定稿剧本和美术设定")

    project = HibtvProject(
        user_id=user.id,
        name=(name.strip() or Path(script_name).stem or "未命名短剧")[:120],
        genre=genre.strip()[:64],
        episodes=max(1, int(episodes or 0)),
        minutes_min=max(1, int(minutes_min or 2)),
        minutes_max=max(int(minutes_min or 2), int(minutes_max or 5)),
        aspect_ratio="16:9" if str(ratio).startswith("16") else "9:16",
        style=(style.strip()[:32] or "写实"),
        novel_name=script_name[:255],
        novel_text=script_text,
        keeps=keeps.strip()[:2000],
        status="draft",
    )
    db.add(project)
    db.flush()

    # 正文落盘：outline 的 input 里给**绝对路径**，绝不把几十万字内联进 prompt
    from ..services.hibtv_sources import save_sources
    save_sources(db, project, script_name, script_text, art_name, art_text)
    db.commit()
    db.refresh(project)
    return _project_payload(db, project)


@router.put("/projects/{project_id}/sources")
def replace_sources(project_id: int, script: UploadFile = File(...), art: UploadFile = File(...),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    sn, st = _read_text(script, "定稿剧本")
    an, at = _read_text(art, "美术设定")
    if not st.strip() or not at.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "剧本与美术设定不能为空")
    from ..services.hibtv_sources import save_sources
    save_sources(db, project, sn, st, an, at)
    project.updated_at = hibtv_jobs.now()
    db.commit()
    return _project_payload(db, project)


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return _project_payload(db, _project(db, project_id, user))


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, body: HibtvProjectPatch,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    if body.novel_text is not None or body.novel_name is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请通过双文件上传替换定稿剧本和美术设定")
    fields = {
        "name": (body.name, str), "genre": (body.genre, str),
        "episodes": (body.episodes, int), "minutes_min": (body.minutes_min, int),
        "minutes_max": (body.minutes_max, int), "aspect_ratio": (body.aspect_ratio, str),
        "style": (body.style, str),
        "keeps": (body.keeps, str), "novel_name": (body.novel_name, str),
        "novel_text": (body.novel_text, str),
    }
    for key, (value, caster) in fields.items():
        if value is None:
            continue
        new = caster(value)
        old = getattr(project, key)
        if old == new:
            continue
        setattr(project, key, new)
        _log_change(db, project.id, f"project:{project.id}", key, old, new)
    # 小说正文改了要同步落盘：outline 的 input 里只给绝对路径，不内联正文
    if body.novel_text is not None:
        hibtv_codex.write_source(project.id, project.novel_text or "")
    project.updated_at = hibtv_jobs.now()
    db.commit()
    return _project_payload(db, project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    for model in (HibtvDoc, HibtvAsset, HibtvSegment, HibtvFrame, HibtvRender, HibtvJob,
                  HibtvChange):
        db.query(model).filter(model.project_id == project.id).delete(synchronize_session=False)
    db.delete(project)
    db.commit()
    try:
        shutil.rmtree(hibtv_jobs.project_dir(project_id), ignore_errors=True)
    except OSError:
        pass
    return None


# ------------------------------------------------------------------ 文档
@router.get("/projects/{project_id}/docs/{kind}")
def get_doc(project_id: int, kind: str, ep: int = Query(default=0, ge=0),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    if kind not in DOC_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知文档类型")
    ep = _doc_ep(kind, ep)
    doc = hibtv_jobs.get_doc(db, project.id, kind, ep)
    if doc is None:
        return {"kind": kind, "ep": ep, "status": "empty", "version": 0,
                "data": {}, "gates": {}, "meta": {}, "error": "", "updated_at": ""}
    return hibtv_jobs.doc_payload(doc)


@router.patch("/projects/{project_id}/docs/{kind}")
def patch_doc(project_id: int, kind: str, body: HibtvDocPatch,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """就地编辑一个字段（双击改文案），写变更底账后可撤销；门结果标「未复跑」。"""
    project = _project(db, project_id, user)
    if kind not in DOC_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知文档类型")
    ep = _doc_ep(kind, body.ep)
    doc = _doc_or_404(db, project, kind, ep)
    data = hibtv_jobs.jloads(doc.data, {})
    try:
        old = hibtv_jobs.set_json_path(data, body.path, body.value)
    except (ValueError, KeyError, IndexError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"路径无效：{exc}") from exc
    _log_change(db, project.id, _doc_key(doc), body.path, old, body.value)
    hibtv_jobs.save_doc(db, project.id, kind, ep, data)
    _mark_stale(db, doc)
    _resync(db, project, kind, ep)
    db.commit()
    return hibtv_jobs.doc_payload(doc)


# ------------------------------------------------------------------ 作业
@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: int, limit: int = Query(default=20, ge=1, le=100),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    rows = (db.query(HibtvJob).filter(HibtvJob.project_id == project.id)
            .order_by(HibtvJob.id.desc()).limit(limit).all())
    return [hibtv_jobs.job_payload(j) for j in rows]


@router.post("/projects/{project_id}/jobs", status_code=status.HTTP_202_ACCEPTED)
def create_job(project_id: int, body: HibtvJobIn,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """投递作业：文本 7 段（codex）、出图（资产单图 / 关键帧）、拼接。"""
    project = _project(db, project_id, user)
    kind = body.kind
    ep = int(body.ep or 0)
    if kind in hibtv_jobs.TEXT_KINDS:
        from ..services.hibtv_sources import get_sources
        sources = get_sources(db, project)
        if not all((sources.get(key) or {}).get("text", "").strip() for key in ("script", "art")):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先补传定稿剧本与美术设定")

    active = (db.query(HibtvJob)
              .filter(HibtvJob.project_id == project.id, HibtvJob.kind == kind,
                      HibtvJob.ep == ep, HibtvJob.status.in_(("queued", "running")))
              .order_by(HibtvJob.id.desc()).first())
    if active is not None:
        return hibtv_jobs.job_payload(active)

    payload = dict(body.payload or {})
    if kind in ("script", "storyboard", "staging") and not ep:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请指定集数")

    if kind in hibtv_jobs.TEXT_KINDS and kind != "outline":
        # 下游 stage 一律要大纲：没大纲就没法「引用上游绝对路径」
        if hibtv_jobs.get_doc(db, project.id, "outline") is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "先跑一次「大纲」，再往下走")
        if not codex_gateway.status()["available"]:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "codex 网关不可用（本平台无本地兜底），请先恢复网关")

    if kind == "images":
        steps = hibtv_jobs.image_steps(db, project, payload)
        if steps <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有需要出图的资产 / 关键帧")
        cost = compute_ad_image_cost(steps)
        if user.credits < cost:
            raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                                f"积分不足：本次出图 {steps} 张需 {cost} 积分")
        if cost:
            add_credits(db, user, -cost, "consume", note=f"短剧出图 {steps} 张")
        payload["cost"] = cost
    if kind == "compose":
        ep = ep or int(payload.get("ep") or 1)
        payload["ep"] = ep

    job = hibtv_jobs.enqueue(db, project.id, kind, ep=ep, payload=payload)
    db.commit()
    db.refresh(job)
    return hibtv_jobs.job_payload(job)


@router.post("/projects/{project_id}/jobs/{job_id}/cancel")
def cancel_job(project_id: int, job_id: int,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    job = db.get(HibtvJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "作业不存在")
    if job.status == "queued":
        job.status = "cancelled"
        job.stage = "已取消"
        job.finished_at = hibtv_jobs.now()
        payload = hibtv_jobs.jloads(job.payload, {})
        cost = int(payload.get("cost") or 0)
        if cost and not payload.get("refunded"):
            add_credits(db, user, cost, "refund", note="短剧作业取消退还")
            payload["refunded"] = True
            job.payload = hibtv_jobs.jdumps(payload)
        db.commit()
    elif job.status == "running":
        # 文本段：先让网关停掉（运行中杀进程组 / 排队中出队），再收敛本地状态。
        # 图像 / 拼接：已产出物保留，执行协程收尾时发现 cancelled 不再覆写为 done。
        if job.kind in hibtv_jobs.TEXT_KINDS:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "网关作业取消是异步的：请用 POST /projects/{id}/jobs/{job_id}/cancel-remote")
        hibtv_jobs.cancel_running(db, project, job)
        db.commit()
    return hibtv_jobs.job_payload(job)


@router.post("/projects/{project_id}/jobs/{job_id}/cancel-remote")
async def cancel_remote_job(project_id: int, job_id: int,
                            user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """取消跑到网关上的文本作业（不调 DELETE：只归档、不删 workdir，产物还能下载）。"""
    project = _project(db, project_id, user)
    job = db.get(HibtvJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "作业不存在")
    await hibtv_codex.cancel(project.id, job)
    job.status = "cancelled"
    job.stage = "已取消（网关已通知）"
    job.finished_at = hibtv_jobs.now()
    db.commit()
    return hibtv_jobs.job_payload(job)


# ------------------------------------------------------------------ codex 产物
@router.get("/projects/{project_id}/artifacts")
def list_artifacts(project_id: int, job_id: int = Query(default=0),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """某条文本作业的全部产物（JSON 折叠树 / md 预览 / report.html iframe / 下载）。"""
    project = _project(db, project_id, user)
    job = _artifact_job(db, project, job_id)
    rows = hibtv_jobs.jloads(job.artifacts, [])
    base = f"/files/hibtv/{project.id}/codex/{job.codex_job_id}"
    items = []
    for row in rows if isinstance(rows, list) else []:
        rel = str((row or {}).get("path") or "")
        if not rel:
            continue
        suffix = Path(rel).suffix.lower()
        items.append({
            "path": rel, "name": Path(rel).name, "bytes": int((row or {}).get("bytes") or 0),
            "kind": ("html" if suffix in (".html", ".htm") else
                     "md" if suffix in (".md",) else
                     "json" if suffix in (".json", ".jsonl") else "other"),
            "url": f"{base}/{rel}",
        })
    return {
        "jobId": job.id, "kind": job.kind, "ep": job.ep, "status": job.status,
        "codexJobId": job.codex_job_id or "",
        "gates": hibtv_jobs.jloads(job.usage, {}),
        "items": items,
        "count": len(items),
    }


@router.get("/projects/{project_id}/artifacts/download")
def download_artifacts(project_id: int, job_id: int = Query(default=0),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """把一条作业的产物打包成 zip（「下载全部」）。"""
    project = _project(db, project_id, user)
    job = _artifact_job(db, project, job_id)
    src = hibtv_codex.job_dir(project.id, job.codex_job_id)
    if not src.exists():
        raise HTTPException(status.HTTP_410_GONE, "产物目录已不存在")
    target = hibtv_jobs.project_dir(project.id) / "codex" / f"job-{job.id}"
    archive = shutil.make_archive(str(target), "zip", root_dir=str(src))
    return FileResponse(archive, media_type="application/zip",
                        filename=f"{job.kind}-E{int(job.ep or 0):02d}-{job.id}.zip")


def _artifact_job(db: Session, project: HibtvProject, job_id: int) -> HibtvJob:
    query = db.query(HibtvJob).filter(HibtvJob.project_id == project.id,
                                      HibtvJob.kind.in_(hibtv_jobs.TEXT_KINDS))
    job = db.get(HibtvJob, int(job_id)) if job_id else None
    if job is None:
        job = query.order_by(HibtvJob.id.desc()).first()
    if job is None or job.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "还没有 codex 产物")
    return job


# ------------------------------------------------------------------ 资产卡
@router.post("/projects/{project_id}/assets", status_code=status.HTTP_201_CREATED)
def add_asset(project_id: int, body: HibtvAssetPatch, kind: str = Query(...),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    if kind not in ("cast", "scene", "prop") or not (body.name or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请选择资产类型并填写名称")
    doc_kind, key, prefix = {"cast": ("cast", "characters", "C"),
                             "scene": ("art", "scenes", "S"), "prop": ("art", "props", "P")}[kind]
    doc = hibtv_jobs.get_doc(db, project.id, doc_kind, 0)
    data = hibtv_jobs.jloads(doc.data, {}) if doc else {}
    rows = data.setdefault(key, [])
    occupied = {a.code for a in db.query(HibtvAsset).filter_by(project_id=project.id, kind=kind)}
    occupied.update(r.get("id") for r in rows)
    n = 1
    while f"{prefix}{n:02d}" in occupied:
        n += 1
    code = f"{prefix}{n:02d}"
    rows.append({**(body.data or {}), "id": code, "name": body.name.strip()[:120]})
    doc = hibtv_jobs.save_doc(db, project.id, doc_kind, 0, data)
    _mark_stale(db, doc, "手动补建资产后需要重新校验")
    _resync(db, project, doc_kind)
    _log_change(db, project.id, _doc_key(doc), key, None, rows[-1])
    db.commit()
    asset = db.query(HibtvAsset).filter_by(project_id=project.id, kind=kind, code=code).one()
    return hibtv_jobs.asset_payload(project.id, asset)


@router.get("/projects/{project_id}/assets")
def list_assets(project_id: int, kind: str = Query(default=""),
                include_hidden: bool = Query(default=False),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    query = db.query(HibtvAsset).filter(HibtvAsset.project_id == project.id)
    if kind:
        query = query.filter(HibtvAsset.kind == kind)
    if not include_hidden:
        query = query.filter(HibtvAsset.hidden.is_(False))
    rows = query.order_by(HibtvAsset.kind, HibtvAsset.sort).all()
    return [hibtv_jobs.asset_payload(project.id, a) for a in rows]


@router.patch("/projects/{project_id}/assets/{asset_id}")
def patch_asset(project_id: int, asset_id: int, body: HibtvAssetPatch,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    asset = db.get(HibtvAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    if asset.kind == "cast" and body.data:
        from ..services.hibtv_contracts import design
        current = hibtv_jobs.jloads(asset.data, {})
        changes = dict(body.data)
        main_prompt = changes.pop("mainPrompt", None)
        if main_prompt is not None:
            tasks = hibtv_jobs.jloads(hibtv_jobs.jdumps(current.get("imageTasks") or {}), {})
            if not tasks.get("front-full") or not str(main_prompt).strip():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先导出角色参考任务，提示词不能为空")
            tasks["front-full"]["prompt"] = str(main_prompt).strip()
        edited = {**current, **changes}
        if design(edited) != design(current):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "角色生产提示词由网关导出，请修改角色文档后重新运行角色参考阶段")
        if main_prompt is not None:
            body.data = {**changes, "imageTasks": tasks}
    if body.name is not None and body.name != asset.name:
        _log_change(db, project.id, f"asset:{asset.id}", "name", asset.name, body.name)
        asset.name = body.name[:120]
    if body.hidden is not None and bool(body.hidden) != bool(asset.hidden):
        _log_change(db, project.id, f"asset:{asset.id}", "hidden", bool(asset.hidden),
                    bool(body.hidden))
        asset.hidden = bool(body.hidden)
    if body.data:
        merged = hibtv_jobs.jloads(asset.data, {})
        old_raw = hibtv_jobs.jdumps(merged)
        merged.update(body.data)
        if asset_input(merged) != asset_input(hibtv_jobs.jloads(asset.data, {})):
            merged["imageConfirmed"] = False
            merged.pop("confirmedHash", None)
            hibtv_assets.mark_stale(asset, "__anchor")
        _log_change(db, project.id, f"asset:{asset.id}", "data", old_raw, merged)
        asset.data = hibtv_jobs.jdumps(merged)
    asset.updated_at = hibtv_jobs.now()
    db.commit()
    return hibtv_jobs.asset_payload(project.id, asset)


@router.post("/projects/{project_id}/assets/{asset_id}/image",
             status_code=status.HTTP_202_ACCEPTED)
def asset_image(project_id: int, asset_id: int, body: HibtvImageIn,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """资产图：`draw`（出锚点）/ `regen`（重出锚点，派生全过期）/ `confirm`（确认锚点）
    / `draw_view`（出指定派生视图，需先确认锚点）。
    """
    project = _project(db, project_id, user)
    asset = db.get(HibtvAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")

    if body.action == "confirm":
        try:
            hibtv_assets.confirm_image(db, project, asset, body.view)
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        asset.updated_at = hibtv_jobs.now()
        db.commit()
        return hibtv_jobs.asset_payload(project.id, asset)

    payload: Dict = {"mode": "assets", "asset_id": asset.id, "kind": asset.kind}
    if body.action == "draw_view":
        view = body.view.strip()
        if not view or hibtv_assets.view_spec(asset.kind, view, hibtv_jobs.jloads(asset.data, {})) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "派生视图 view 非法")
        if hibtv_assets.anchor_id(asset.kind) == view:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "锚点请用 draw/regen 出图")
        data = hibtv_jobs.jloads(asset.data, {})
        if not data.get("imageConfirmed"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "先确认锚点，再出派生视图")
        if not (asset.main_path or "").strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "锚点还没出图，不能派生视图")
        payload["view"] = view
    elif body.hint.strip():
        data = hibtv_jobs.jloads(asset.data, {})
        hints = data.get("hints") or {}
        hints["__main"] = body.hint.strip()[:400]
        data["hints"] = hints
        asset.data = hibtv_jobs.jdumps(data)

    steps = hibtv_jobs.image_steps(db, project, payload)
    if steps <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有需要出图的资产")
    cost = compute_ad_image_cost(steps)
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"积分不足：本次出图 {steps} 张需 {cost} 积分")
    if cost:
        add_credits(db, user, -cost, "consume", note=f"短剧资产图 {steps} 张")
    payload["cost"] = cost
    job = hibtv_jobs.enqueue(db, project.id, "images", payload=payload, ref=asset.code)
    db.commit()
    db.refresh(job)
    return hibtv_jobs.job_payload(job)


@router.post("/projects/{project_id}/assets/{asset_id}/upload")
async def upload_asset(project_id: int, asset_id: int,
                       file: UploadFile = File(...),
                       view: str = Form(""),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """手工上传一张图顶掉锚点主图（view 为空）或指定派生视图。"""
    project = _project(db, project_id, user)
    asset = db.get(HibtvAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 JPG / PNG / WebP 图片")
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片不能超过 20MB")

    view = (view or "").strip()
    is_anchor = not view or view == hibtv_assets.anchor_id(asset.kind)
    if view and hibtv_assets.view_spec(asset.kind, view, hibtv_jobs.jloads(asset.data, {})) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未知资产视图")
    if not is_anchor and not hibtv_assets.main_current(project, asset):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "先确认当前锚点再上传派生图")
    fname = ("main" if is_anchor else view) + "-" + uuid4().hex
    rel = f"assets/{asset.kind}/{asset.code or asset.id}/{fname}{suffix}"
    path = hibtv_jobs.project_dir(project.id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _log_change(db, project.id, f"asset:{asset.id}",
                "main" if is_anchor else f"view:{view}",
                asset.main_path if is_anchor else "", rel)
    hibtv_assets.import_image(db, project, asset, path, rel,
                              view="" if is_anchor else view)
    db.commit()
    return hibtv_jobs.asset_payload(project.id, asset)


# ------------------------------------------------------------------ 关键帧
@router.get("/projects/{project_id}/frames")
def list_frames(project_id: int, ep: int = Query(default=1, ge=1),
                seg_id: str = Query(default=""),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    query = db.query(HibtvFrame).filter(HibtvFrame.project_id == project.id,
                                        HibtvFrame.ep == int(ep))
    if seg_id:
        query = query.filter(HibtvFrame.seg_id == seg_id)
    rows = query.order_by(HibtvFrame.seg_id, HibtvFrame.cut_index).all()
    rows = [f for f in rows if hibtv_jobs.jloads(f.meta, {}).get("active") is not False]
    seg_ids = sorted({row.seg_id for row in rows})
    planned = {
        int(row["frameId"]): row
        for current in seg_ids
        for row in hibtv_frames.plan(db, project, current, ep)
    }
    items = []
    for frame in rows:
        item = _frame_payload(project.id, frame)
        route_row = planned.get(frame.id, {})
        item["ready"] = bool(route_row.get("ready", hibtv_frames.frame_current(db, project, frame)))
        item["references"] = route_row.get("references", [])
        item["refsCustomized"] = bool(route_row.get("refsCustomized"))
        if not item["ready"] and frame.path:
            item["status"] = "stale"
        items.append(item)
    return {"ep": ep, "frames": items,
            "done": sum(bool(item["ready"]) for item in items), "total": len(rows)}


@router.get("/projects/{project_id}/reference-assets")
def reference_assets(project_id: int, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """镜头引用入口可选的单视图资产。"""
    project = _project(db, project_id, user)
    out: List[Dict] = []
    rows = (db.query(HibtvAsset)
            .filter(HibtvAsset.project_id == project.id, HibtvAsset.hidden.is_(False))
            .order_by(HibtvAsset.kind, HibtvAsset.sort, HibtvAsset.id).all())
    for asset in rows:
        data = hibtv_jobs.jloads(asset.data, {})
        anchor = hibtv_assets.anchor_id(asset.kind)
        if hibtv_assets.main_current(project, asset):
            rel = str(asset.main_path or "")
            out.append({
                "assetId": asset.id, "kind": asset.kind, "code": asset.code,
                "name": asset.name, "view": anchor,
                "viewLabel": "场景主图" if asset.kind == "scene" else "锚点",
                "role": ("scene" if asset.kind == "scene" else
                         "cast-anchor" if asset.kind == "cast" else "prop"),
                "uploadId": int(asset.main_upload_id or 0),
                "url": f"/files/hibtv/{project.id}/{rel}", "version": int(asset.version or 0),
            })
        for spec in hibtv_assets.view_defs(asset.kind, data):
            view = str(spec.get("id") or "")
            if not view or view == anchor or not hibtv_assets.view_current(project, asset, view):
                continue
            item = hibtv_jobs.jloads(asset.views, {}).get(view) or {}
            rel = str(item.get("path") or "")
            if not rel:
                continue
            role = ("scene-top" if asset.kind == "scene" and view == "top" else
                    "cast-face" if asset.kind == "cast" and view == "face-front" else
                    "cast-anchor" if asset.kind == "cast" else
                    "prop" if asset.kind == "prop" else "scene")
            out.append({
                "assetId": asset.id, "kind": asset.kind, "code": asset.code,
                "name": asset.name, "view": view,
                "viewLabel": ("定场图" if asset.kind == "scene" and view == "top"
                              else str(spec.get("label") or view)),
                "role": role, "uploadId": int(item.get("uploadId") or 0),
                "url": f"/files/hibtv/{project.id}/{rel}",
                "version": int(item.get("version") or 0),
            })
    return out


def _frame_payload(project_id: int, frame: HibtvFrame) -> Dict:
    rel = (frame.path or "").strip()
    meta = hibtv_jobs.jloads(frame.meta, {})
    return {
        "id": frame.id, "ep": frame.ep, "segId": frame.seg_id,
        "cutIndex": int(frame.cut_index), "status": frame.status or "empty",
        "prompt": frame.prompt or "",
        "url": f"/files/hibtv/{project_id}/{rel}" if rel else "",
        "uploadId": int(frame.upload_id or 0), "version": int(frame.version or 0),
        "meta": meta,
    }


def _frame_or_404(db: Session, project: HibtvProject, frame_id: int) -> HibtvFrame:
    frame = db.get(HibtvFrame, frame_id)
    if (frame is None or frame.project_id != project.id
            or hibtv_jobs.jloads(frame.meta, {}).get("active") is False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关键帧不存在")
    return frame


@router.post("/projects/{project_id}/frames/{frame_id}/image",
             status_code=status.HTTP_202_ACCEPTED)
def frame_image(project_id: int, frame_id: int, body: HibtvFrameImageIn,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """关键帧出图 / 单张重出（提示词可选覆盖，写进变更底账）。"""
    project = _project(db, project_id, user)
    frame = _frame_or_404(db, project, frame_id)
    if body.prompt.strip():
        old = frame.prompt
        _log_change(db, project.id, f"frame:{frame.id}", "prompt", old,
                    body.prompt.strip())
        frame.prompt = body.prompt.strip()
        meta = hibtv_jobs.jloads(frame.meta, {})
        meta["overridePrompt"] = frame.prompt
        meta["stale"] = bool(frame.path)
        frame.meta = hibtv_jobs.jdumps(meta)
    payload: Dict = {"mode": "frames", "frame_ids": [frame.id],
                     "ep": frame.ep, "seg_id": frame.seg_id}
    steps = hibtv_jobs.image_steps(db, project, payload)
    if steps <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这一帧不需要出图")
    cost = compute_ad_image_cost(steps)
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"积分不足：本帧出图需 {cost} 积分")
    if cost:
        add_credits(db, user, -cost, "consume", note="短剧关键帧 1 张")
    payload["cost"] = cost
    job = hibtv_jobs.enqueue(db, project.id, "images", payload=payload,
                             ref=f"{frame.seg_id}-f{int(frame.cut_index)}")
    db.commit()
    db.refresh(job)
    return hibtv_jobs.job_payload(job)


@router.patch("/projects/{project_id}/frames/{frame_id}/references")
def patch_frame_references(project_id: int, frame_id: int,
                           body: HibtvFrameReferencesIn,
                           user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """设置单个镜头的引用素材；空列表恢复自动挂载。"""
    project = _project(db, project_id, user)
    frame = _frame_or_404(db, project, frame_id)
    resolved = []
    selected = []
    for item in body.items:
        if not isinstance(item, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "引用素材格式不正确")
        try:
            ref = hibtv_frames.resolve_reference_item(db, project, item)
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        resolved.append(ref)
        selected.append({
            "assetId": ref["assetId"], "view": ref.get("view") or "",
            "role": ref.get("role") or "", "note": ref.get("note") or "",
        })
    if len(resolved) > hibtv_frames.MAX_VIDEO_REFS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"单镜引用素材不能超过 {hibtv_frames.MAX_VIDEO_REFS} 张",
        )
    old = frame.refs
    frame.refs = hibtv_jobs.jdumps(selected)
    meta = hibtv_jobs.jloads(frame.meta, {})
    meta["refsCustomized"] = bool(selected)
    meta["stale"] = bool(frame.path)
    frame.meta = hibtv_jobs.jdumps(meta)
    frame.updated_at = hibtv_jobs.now()
    _log_change(db, project.id, f"frame:{frame.id}", "refs", old, frame.refs)
    db.commit()
    item = _frame_payload(project.id, frame)
    item["references"] = [
        {
            "assetId": ref["assetId"], "kind": ref["kind"], "code": ref["code"],
            "name": ref["name"], "role": ref["role"], "view": ref["view"],
            "note": ref.get("note") or "", "uploadId": ref["uploadId"],
        }
        for ref in resolved
    ]
    item["refsCustomized"] = bool(selected)
    return item


@router.post("/projects/{project_id}/frames/{frame_id}/upload")
async def upload_frame(project_id: int, frame_id: int,
                       file: UploadFile = File(...),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """手工上传一张关键帧顶掉这一张。"""
    project = _project(db, project_id, user)
    frame = _frame_or_404(db, project, frame_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 JPG / PNG / WebP 图片")
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片不能超过 20MB")
    rel = f"frames/{frame.seg_id}/f{int(frame.cut_index)}-{uuid4().hex}{suffix}"
    path = hibtv_jobs.project_dir(project.id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    frame.path = rel
    frame.upload_id = hibtv_jobs.register_upload(db, project.user_id, path) or 0
    frame.status = "review"
    meta = hibtv_jobs.jloads(frame.meta, {})
    meta.update({"uploaded": True, "stale": False, "sha256": file_hash(path),
                 "renderedPlanHash": meta.get("planHash"), "dependencies": [],
                 "contextHash": hibtv_frames.context_hash(db, project, frame.ep)})
    frame.meta = hibtv_jobs.jdumps(meta)
    frame.version = int(frame.version or 0) + 1
    frame.updated_at = hibtv_jobs.now()
    db.commit()
    return _frame_payload(project.id, frame)


# ------------------------------------------------------------------ 段与出片
def _segment_or_404(db: Session, project: HibtvProject, seg_id: str) -> HibtvSegment:
    seg = (db.query(HibtvSegment)
           .filter(HibtvSegment.project_id == project.id, HibtvSegment.seg_id == seg_id)
           .first())
    if seg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "段不存在")
    return seg


def _current_version(seg: HibtvSegment) -> Dict:
    versions = hibtv_jobs.jloads(seg.versions, [])
    for v in versions:
        if int(v.get("v") or 0) == int(seg.current_version or 0):
            return v
    return versions[-1] if versions else {}


@router.get("/projects/{project_id}/segments")
def list_segments(project_id: int, ep: int = Query(default=1, ge=1),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    hibtv_jobs.sync_segment_tasks(db, project, ep)
    rows = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id, HibtvSegment.ep == ep)
            .order_by(HibtvSegment.idx).all())
    rows = [row for row in rows if hibtv_jobs.jloads(row.data, {}).get("active") is not False]
    return {
        "ep": ep,
        "segments": [hibtv_jobs.segment_payload(project.id, s) for s in rows],
        "done": len([s for s in rows if s.status == "done"]),
        "total": len(rows),
    }


@router.get("/projects/{project_id}/segments/{seg_id}")
def get_segment(project_id: int, seg_id: str,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    hibtv_jobs.sync_segment_tasks(db, project, seg.ep)
    db.refresh(seg)
    return _segment_detail(db, project, seg)


def _segment_detail(db: Session, project: HibtvProject, seg: HibtvSegment) -> Dict:
    payload = hibtv_jobs.segment_payload(project.id, seg)
    frames = hibtv_frames.plan(db, project, seg.seg_id, ep=seg.ep)
    payload["frames"] = frames
    payload["routeMode"] = "A-assets"
    payload["missingFrames"] = [f["cutIndex"] for f in frames
                                if not f.get("ready")]
    payload["framesReady"] = bool(frames) and not payload["missingFrames"]
    # 出片请求值：H3 只出 5+17n 帧（24fps），13s 上限 = 311 帧 = 12.9583s
    plan_seconds = float(payload["planSeconds"] or 0)
    frames_n = h3_time.frames_for(plan_seconds or float(settings.hibtv_seg_max_seconds))
    payload["request"] = {"frames": frames_n, "seconds": round(frames_n / 24.0, 4)}
    payload["capSeconds"] = float(settings.hibtv_seg_max_seconds)
    return payload


@router.post("/projects/{project_id}/segments/{seg_id}/generate",
             status_code=status.HTTP_202_ACCEPTED)
def generate_segment(project_id: int, seg_id: str, body: HibtvSegmentGenerateIn,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """段级出片：路线 A，每镜素材汇总后一次 ref2v 生成。"""
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    data = hibtv_jobs.jloads(seg.data, {})
    prompt = (body.prompt or data.get("h3Prompt") or "").strip()
    if not prompt:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "本段还没有 H3 提示词，先跑分镜")
    if body.prompt and body.prompt.strip() != str(data.get("h3Prompt") or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "提示词修改需回到网关重生成并校验，不能绕过当前分镜合同")
    try:
        expected = next((row for row in current_segments(db, project, seg.ep, checked=True)
                         if row["id"] == seg_id), None)
        if expected is None or digest(segment_input(expected)) != digest(segment_input(data)):
            raise RuntimeError("当前分镜与段表不一致，请重新导入")
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    cuts = data.get("cuts") or []
    plan = round(sum(float(c.get("seconds") or 0) for c in cuts), 3)
    cap = float(settings.hibtv_seg_max_seconds)
    if plan > cap + 1e-6 or data.get("oversize"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"本段计划 {plan:.1f}s，超过单次生成上限 {cap:g}s：“先拆镜头再出片”，"
            f"超限请重跑本集 storyboard（平台不拆段、不改提示词）")

    frames_n = data.get("generationFrames")
    if (not isinstance(frames_n, int) or isinstance(frames_n, bool) or frames_n < 5
            or (frames_n - 5) % 17 or abs(frames_n / 24 - plan) > 0.001):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "分镜时长未对齐 H3 合法帧预算，请在网关重跑分镜")
    try:
        refs = hibtv_frames.route_a_reference_plan(db, project, seg)
        if any(not int(row.get("uploadId") or 0) for row in refs):
            raise RuntimeError(f"{seg.seg_id} 有引用素材缺少 Upload 记录")
        ref_ids = [int(row["uploadId"]) for row in refs]
        ref_source = "assets" if ref_ids else "none"
        task_prompt = hibtv_frames.route_a_prompt(db, project, seg, refs)
        input_hash = hibtv_frames.video_input_hash(
            db, project, seg, refs=refs, prompt=task_prompt
        )
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    seconds = round(frames_n / 24.0, 4)
    cost = compute_cost(seconds, "768p")
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"积分不足：本段 {seconds:g}s 需 {cost} 积分，请先充值")

    task = Task(
        user_id=user.id,
        mode="r2v",
        prompt=task_prompt,                  # 路线 A：镜头素材 + 段级镜头时间线
        aspect_ratio=project.aspect_ratio or "9:16",
        duration=max(5, int(round(seconds))),
        frames=frames_n,                     # worker 用帧数回算秒数，避免被向上吸附超限
        resolution="768p",
        enhance=False,                       # h3Prompt 已逐字对齐，不再叠云端增强
        scene="drama",
        ref_image_ids=json.dumps(ref_ids) if ref_ids else "",
        status="queued",
    )
    db.add(task)
    db.flush()

    versions = hibtv_jobs.jloads(seg.versions, [])
    version = max([int(v.get("v") or 0) for v in versions] or [0]) + 1
    versions.append({
        "v": version, "task_id": task.id, "path": "", "seconds": 0,
        "inputHash": input_hash, "segmentHash": digest(segment_input(data)),
        "frames": frames_n, "refImageIds": ref_ids,
        "at": hibtv_jobs.now().strftime("%Y-%m-%d %H:%M"),
    })
    seg.versions = hibtv_jobs.jdumps(versions)
    seg.status = "running"
    seg.updated_at = hibtv_jobs.now()
    db.commit()
    return {
        "ok": True, "taskId": task.id, "v": version, "cost": cost,
        "seconds": seconds, "frames": frames_n, "refs": len(ref_ids),
        "refSource": ref_source,
        "planSeconds": plan,
        "route": "A-assets",
    }


@router.post("/projects/{project_id}/segments/{seg_id}/rebuild")
def rebuild_segment(project_id: int, seg_id: str,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """镜头重修 = **重跑本集 storyboard**。

    平台不再手搓 h3Prompt（那会把 prompt.md 的「切点↔图」契约改坏）：
    改完镜头就重新跑这一集的 storyboard，产物回来再逐段对账。
    """
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    if not codex_gateway.status()["available"]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "codex 网关不可用，无法重跑分镜")
    job = hibtv_jobs.enqueue(db, project.id, "storyboard", ep=seg.ep,
                             payload={"hint": f"本集第 {seg.seg_id} 段需要重修镜头"})
    db.commit()
    db.refresh(job)
    return hibtv_jobs.job_payload(job)


@router.post("/projects/{project_id}/segments/{seg_id}/choose")
def choose_version(project_id: int, seg_id: str, body: HibtvSegmentChooseIn,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    versions = hibtv_jobs.jloads(seg.versions, [])
    selected = next((v for v in versions if int(v.get("v") or 0) == int(body.v)), None)
    if selected is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在")
    try:
        if not selected.get("path") or selected.get("inputHash") != hibtv_frames.video_input_hash(db, project, seg):
            raise RuntimeError("该视频版本依赖旧输入或尚未完成，不能作为当前成片")
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    seg.current_version = int(body.v)
    seg.updated_at = hibtv_jobs.now()
    db.commit()
    return hibtv_jobs.segment_payload(project.id, seg)


# ------------------------------------------------------------------ 整集拼接
@router.post("/projects/{project_id}/episodes/{ep}/render",
             status_code=status.HTTP_202_ACCEPTED)
def render_episode(project_id: int, ep: int,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    hibtv_jobs.sync_segment_tasks(db, project, ep)
    try:
        expected = current_segments(db, project, ep, checked=True)
        by_id = {s.seg_id: s for s in db.query(HibtvSegment).filter(
            HibtvSegment.project_id == project.id, HibtvSegment.ep == ep).all()}
        for row in expected:
            seg = by_id.get(row["id"])
            if seg is None:
                raise RuntimeError(f"{row['id']} 缺少当前分段记录")
            version = _current_version(seg)
            if not version.get("path") or version.get("inputHash") != hibtv_frames.video_input_hash(db, project, seg):
                raise RuntimeError(f"{row['id']} 尚无与当前输入匹配的视频，请先生成")
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    compose_status = hibtv_compose.status()
    if not compose_status["available"]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, compose_status["note"])

    active = (db.query(HibtvJob)
              .filter(HibtvJob.project_id == project.id, HibtvJob.kind == "compose",
                      HibtvJob.ep == ep, HibtvJob.status.in_(("queued", "running")))
              .order_by(HibtvJob.id.desc()).first())
    if active is not None:
        return hibtv_jobs.job_payload(active)
    job = hibtv_jobs.enqueue(db, project.id, "compose", ep=ep, payload={"ep": ep})
    db.commit()
    db.refresh(job)
    return hibtv_jobs.job_payload(job)


@router.get("/projects/{project_id}/episodes/{ep}/renders")
def list_renders(project_id: int, ep: int,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    rows = (db.query(HibtvRender)
            .filter(HibtvRender.project_id == project.id, HibtvRender.ep == ep)
            .order_by(HibtvRender.id.desc()).all())
    out: List[Dict] = []
    for row in rows:
        rel = row.path or ""
        out.append({
            "id": row.id, "ep": row.ep, "version": row.version,
            "path": rel, "url": f"/files/hibtv/{project.id}/{rel}" if rel else "",
            "durationSec": float(row.duration_sec or 0), "size": int(row.size or 0),
            "cutlist": hibtv_jobs.jloads(row.cutlist, []),
            "createdAt": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
        })
    return out


# ------------------------------------------------------------------ 变更
@router.get("/projects/{project_id}/changes")
def list_changes(project_id: int, limit: int = Query(default=100, ge=1, le=500),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    rows = (db.query(HibtvChange)
            .filter(HibtvChange.project_id == project.id)
            .order_by(HibtvChange.id.desc()).limit(limit).all())
    return [{
        "id": c.id, "who": c.who, "what": c.what, "path": c.path,
        "from": hibtv_jobs.jloads(c.from_value, None),
        "to": hibtv_jobs.jloads(c.to_value, None),
        "reverted": bool(c.reverted),
        "at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
    } for c in rows]


@router.post("/projects/{project_id}/changes/{change_id}/undo")
def undo_change(project_id: int, change_id: int,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    change = db.get(HibtvChange, change_id)
    if change is None or change.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "变更不存在")
    if change.reverted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该变更已撤销")
    old = hibtv_jobs.jloads(change.from_value, None)
    what = change.what or ""

    if what.startswith("asset:"):
        asset = db.get(HibtvAsset, int(what.split(":", 1)[1]))
        if asset is None or asset.project_id != project.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
        if change.path == "data":
            asset.data = old if isinstance(old, str) else hibtv_jobs.jdumps(old)
        else:
            setattr(asset, change.path, old)
        asset.updated_at = hibtv_jobs.now()
    elif what.startswith("frame:"):
        frame = db.get(HibtvFrame, int(what.split(":", 1)[1]))
        if frame is None or frame.project_id != project.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "关键帧不存在")
        setattr(frame, change.path, old)
        if change.path == "refs":
            meta = hibtv_jobs.jloads(frame.meta, {})
            refs = hibtv_jobs.jloads(frame.refs, [])
            meta["refsCustomized"] = bool(
                isinstance(refs, list)
                and any(isinstance(item, dict) and item.get("assetId") for item in refs)
            )
            meta["stale"] = bool(frame.path)
            frame.meta = hibtv_jobs.jdumps(meta)
        frame.updated_at = hibtv_jobs.now()
    elif what.startswith("project:"):
        setattr(project, change.path, old)
        project.updated_at = hibtv_jobs.now()
    else:
        kind, _, ep_raw = what.partition("#")
        ep = int(ep_raw or 0)
        if kind not in DOC_KINDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "该变更不支持撤销")
        doc = hibtv_jobs.get_doc(db, project.id, kind, ep)
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
        data = hibtv_jobs.jloads(doc.data, {})
        hibtv_jobs.set_json_path(data, change.path, old)
        hibtv_jobs.save_doc(db, project.id, kind, ep, data)
        _mark_stale(db, doc, "撤销后门未复跑")
        _resync(db, project, kind, ep)

    change.reverted = True
    db.commit()
    return {"ok": True, "id": change.id, "what": what, "path": change.path}


# ------------------------------------------------------------------ 素材库
@router.get("/projects/{project_id}/library")
def project_library(project_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """本项目产出的图 / 片（素材库页）：资产主图 + 关键帧 + 段成片 + 整集成片。"""
    project = _project(db, project_id, user)
    items: List[Dict] = []
    assets = (db.query(HibtvAsset)
              .filter(HibtvAsset.project_id == project.id, HibtvAsset.hidden.is_(False))
              .order_by(HibtvAsset.kind, HibtvAsset.sort).all())
    for asset in assets:
        rel = (asset.main_path or "").strip()
        if not rel:
            continue
        items.append({
            "id": f"a{asset.id}", "kind": "image", "group": asset.kind,
            "code": asset.code, "name": asset.name,
            "url": f"/files/hibtv/{project.id}/{rel}", "version": int(asset.version or 0),
            "stale": False, "at": hibtv_jobs.jloads(asset.main_meta, {}).get("at") or "",
        })
    frames = (db.query(HibtvFrame)
              .filter(HibtvFrame.project_id == project.id)
              .order_by(HibtvFrame.ep, HibtvFrame.seg_id, HibtvFrame.cut_index).all())
    for frame in frames:
        rel = (frame.path or "").strip()
        if not rel:
            continue
        items.append({
            "id": f"f{frame.id}", "kind": "image", "group": "frame",
            "code": f"{frame.seg_id}-f{int(frame.cut_index)}",
            "name": f"{frame.seg_id} 关键帧 f{int(frame.cut_index)}",
            "url": f"/files/hibtv/{project.id}/{rel}", "version": int(frame.version or 0),
            "stale": False, "at": hibtv_jobs.jloads(frame.meta, {}).get("at") or "",
        })
    segs = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id)
            .order_by(HibtvSegment.ep, HibtvSegment.idx).all())
    for seg in segs:
        for v in hibtv_jobs.jloads(seg.versions, []):
            rel = v.get("path") or ""
            if not rel:
                continue
            items.append({
                "id": f"s{seg.id}-{v.get('v')}", "kind": "video", "group": "segment",
                "code": seg.seg_id, "name": f"{seg.seg_id} v{v.get('v')}",
                "url": f"/files/hibtv/{project.id}/{rel}", "version": v.get("v") or 0,
                "stale": False, "at": v.get("at") or "",
            })
    renders = (db.query(HibtvRender)
               .filter(HibtvRender.project_id == project.id)
               .order_by(HibtvRender.id.desc()).all())
    for row in renders:
        rel = row.path or ""
        if not rel:
            continue
        items.append({
            "id": f"r{row.id}", "kind": "video", "group": "render",
            "code": f"E{row.ep:02d}", "name": f"第 {row.ep} 集 v{row.version}",
            "url": f"/files/hibtv/{project.id}/{rel}", "version": row.version,
            "stale": False,
            "at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
        })
    return {"items": items, "count": len(items)}


# ------------------------------------------------------------------ 导出
@router.get("/projects/{project_id}/export")
def export_project(project_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """导出整包清单（前端据此下载 JSON / 逐项取片）。"""
    project = _project(db, project_id, user)
    docs = (db.query(HibtvDoc).filter(HibtvDoc.project_id == project.id)
            .order_by(HibtvDoc.kind, HibtvDoc.ep).all())
    renders = (db.query(HibtvRender).filter(HibtvRender.project_id == project.id)
               .order_by(HibtvRender.ep, HibtvRender.version).all())
    frames = (db.query(HibtvFrame).filter(HibtvFrame.project_id == project.id)
              .order_by(HibtvFrame.ep, HibtvFrame.seg_id, HibtvFrame.cut_index).all())
    return {
        "project": _project_payload(db, project),
        "docs": {
            _doc_key(d): {
                "status": d.status, "version": d.version,
                "gates": hibtv_jobs.jloads(d.gates, {}),
                "meta": hibtv_jobs.jloads(d.meta, {}),
                "data": hibtv_jobs.jloads(d.data, {}),
            }
            for d in docs
        },
        "frames": [_frame_payload(project.id, f) for f in frames],
        "renders": [{
            "ep": r.ep, "version": r.version,
            "url": f"/files/hibtv/{project.id}/{r.path}",
            "durationSec": float(r.duration_sec or 0),
            "cutlist": hibtv_jobs.jloads(r.cutlist, []),
        } for r in renders],
    }

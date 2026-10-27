"""短剧工作台 API（`/api/hibtv`），入口是前端的暗金工作台 `/hibtv/*`。

链路：项目（定稿剧本 + 美术设定）→ 大纲 → 角色 / 美术 → 剧本 → 分镜 → 段级出片 → 整集拼接。

约定：
- 文档（outline / cast / art / script / storyboard）原样保存 shuohao-skills 的 schema JSON；
- LLM / 出图 / 拼接统一走 `hibtv_jobs` 作业表，前端 4s 轮询；
- 段视频复用平台 Task（mode=r2v），Worker 领取时按 `compute_cost` 扣积分；
- 就地编辑、删除、上传替换都写 `hibtv_changes` 底账，可在变更页撤销。
"""
import json
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
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import (
    HibtvAsset,
    HibtvChange,
    HibtvDoc,
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
    HibtvImageIn,
    HibtvJobIn,
    HibtvProjectPatch,
    HibtvSegmentChooseIn,
    HibtvSegmentGenerateIn,
)
from ..services import (
    hibtv_compose,
    hibtv_gates,
    hibtv_image,
    hibtv_jobs,
    hibtv_llm,
    hibtv_parse,
)
from ..services.billing import add_credits, compute_ad_image_cost, compute_cost

router = APIRouter(prefix="/api/hibtv", tags=["hibtv"])

DOC_KINDS = ("outline", "cast", "art", "script", "storyboard")
TEXT_EXTS = {".md", ".markdown", ".txt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024


# ------------------------------------------------------------------ 通用
def _project(db: Session, project_id: int, user: User) -> HibtvProject:
    project = db.get(HibtvProject, project_id)
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    return project


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
        "aspectRatio": project.aspect_ratio or "9:16",
        "scriptName": project.script_name or "",
        "artName": project.art_name or "",
        "keeps": project.keeps or "",
        "detected": hibtv_jobs.jloads(project.detected, {}),
        "status": project.status,
        "createdAt": project.created_at.strftime("%Y-%m-%d %H:%M") if project.created_at else "",
        "updatedAt": project.updated_at.strftime("%Y-%m-%d %H:%M") if project.updated_at else "",
        "docs": {
            _doc_key(d): {
                "status": d.status, "version": d.version,
                "gates": hibtv_jobs.jloads(d.gates, {}),
                "error": d.error or "",
            }
            for d in docs
        },
        "assetCount": asset_count,
        "segmentCount": {
            "total": len(segs),
            "done": len([s for s in segs if s.status == "done"]),
        },
    }


def _read_text(file: Optional[UploadFile], label: str) -> tuple:
    """读上传的文本稿（.md/.txt），返回 (文件名, 正文)。"""
    if file is None:
        return "", ""
    name = file.filename or ""
    suffix = Path(name).suffix.lower()
    if suffix not in TEXT_EXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}仅支持 .md / .txt 文本文件")
    content = file.file.read() if hasattr(file.file, "read") else b""
    if len(content) > MAX_TEXT_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}不能超过 4MB")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return name, content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{label}编码无法识别，请另存为 UTF-8")


def _save_source(project_id: int, stem: str, filename: str, text: str) -> str:
    if not text:
        return ""
    rel = f"source/{stem}{Path(filename).suffix.lower() or '.md'}"
    path = hibtv_jobs.project_dir(project_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


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


# ------------------------------------------------------------------ 状态
@router.get("/status")
def hibtv_status(user: User = Depends(get_current_user)):
    """链路可用性：LLM / 生图节点 / node 质量门 / ffmpeg，前端据此标注降级。"""
    return {
        "enabled": bool(settings.hibtv_enabled),
        "llm": {
            "enabled": settings.hibtv_llm_enabled,
            "provider": settings.hibtv_llm_provider_name,
            "model": settings.hibtv_llm_model_name,
            "mode": settings.hibtv_llm_mode,
        },
        "image": hibtv_image.status(),
        "gates": hibtv_gates.status(),
        "compose": hibtv_compose.status(),
        "segMaxSeconds": int(settings.hibtv_seg_max_seconds),
        "anchorConfirm": bool(settings.hibtv_anchor_confirm),
        "cost": {
            "image": int(settings.cost_ad_image or 0),
            "video": {str(d): compute_cost(d, "768p") for d in (5, 8, 10, 15)},
        },
        "credits": int(user.credits or 0),
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
    keeps: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """新建项目：一次上传定稿剧本 + 美术设定，立即跑确定性识别。"""
    if not settings.hibtv_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "短剧工作台未启用")
    script_name, script_text = _read_text(script, "定稿剧本")
    art_name, art_text = _read_text(art, "美术设定")
    if not script_text.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请上传定稿剧本（.md / .txt）")

    project = HibtvProject(
        user_id=user.id,
        name=(name.strip() or Path(script_name).stem or "未命名短剧")[:120],
        genre=genre.strip()[:64],
        episodes=max(1, int(episodes or 0)),
        minutes_min=max(1, int(minutes_min or 2)),
        minutes_max=max(int(minutes_min or 2), int(minutes_max or 5)),
        aspect_ratio="16:9" if str(ratio).startswith("16") else "9:16",
        script_name=script_name[:255],
        script_text=script_text,
        art_name=art_name[:255],
        art_text=art_text,
        keeps=keeps.strip()[:2000],
        status="draft",
    )
    db.add(project)
    db.flush()

    project.script_path = _save_source(project.id, "script", script_name or "script.md", script_text)
    if art_text:
        project.art_path = _save_source(project.id, "art", art_name or "art.md", art_text)

    parsed_script = hibtv_parse.parse_script(script_text)
    parsed_art = hibtv_parse.parse_art(art_text)
    detected = hibtv_parse.detect_summary(parsed_script, parsed_art)
    project.detected = hibtv_jobs.jdumps(detected)
    if not episodes:
        project.episodes = max(1, int(detected.get("episodes") or 1))
    db.commit()
    db.refresh(project)
    payload = _project_payload(db, project)
    payload["detected"] = detected
    return payload


@router.get("/projects/{project_id}")
def get_project(project_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    return _project_payload(db, _project(db, project_id, user))


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, body: HibtvProjectPatch,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    fields = {
        "name": (body.name, str), "genre": (body.genre, str),
        "episodes": (body.episodes, int), "minutes_min": (body.minutes_min, int),
        "minutes_max": (body.minutes_max, int), "aspect_ratio": (body.aspect_ratio, str),
        "keeps": (body.keeps, str),
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
    project.updated_at = hibtv_jobs.now()
    db.commit()
    return _project_payload(db, project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    for model in (HibtvDoc, HibtvAsset, HibtvSegment, HibtvRender, HibtvJob, HibtvChange):
        db.query(model).filter(model.project_id == project.id).delete(synchronize_session=False)
    db.delete(project)
    db.commit()
    try:
        import shutil

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
    if kind in ("script", "storyboard") and ep <= 0:
        ep = 1
    doc = hibtv_jobs.get_doc(db, project.id, kind, ep)
    if doc is None:
        return {"kind": kind, "ep": ep, "status": "empty", "version": 0,
                "data": {}, "gates": {}, "error": "", "updated_at": ""}
    return hibtv_jobs.doc_payload(doc)


@router.patch("/projects/{project_id}/docs/{kind}")
def patch_doc(project_id: int, kind: str, body: HibtvDocPatch,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """就地编辑一个字段（双击改文案），写变更底账后可撤销。"""
    project = _project(db, project_id, user)
    if kind not in DOC_KINDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知文档类型")
    ep = int(body.ep or 0)
    if kind in ("script", "storyboard") and ep <= 0:
        ep = 1
    doc = _doc_or_404(db, project, kind, ep)
    data = hibtv_jobs.jloads(doc.data, {})
    try:
        old = hibtv_jobs.set_json_path(data, body.path, body.value)
    except (ValueError, KeyError, IndexError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"路径无效：{exc}") from exc
    _log_change(db, project.id, _doc_key(doc), body.path, old, body.value)
    hibtv_jobs.save_doc(db, project.id, kind, ep, data)
    _resync(db, project, kind, ep)
    db.commit()
    return hibtv_jobs.doc_payload(_doc_or_404(db, project, kind, ep))


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
    """投递作业：LLM 类（大纲/角色/美术/剧本/分镜）、出图、拼接。"""
    project = _project(db, project_id, user)
    kind = body.kind
    ep = int(body.ep or 0)

    active = (db.query(HibtvJob)
              .filter(HibtvJob.project_id == project.id, HibtvJob.kind == kind,
                      HibtvJob.ep == ep, HibtvJob.status.in_(("queued", "running")))
              .order_by(HibtvJob.id.desc()).first())
    if active is not None:
        return hibtv_jobs.job_payload(active)

    if kind != "outline" and kind != "images" and kind != "compose":
        if hibtv_jobs.get_doc(db, project.id, "outline") is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "先跑一次「大纲」，再往下走")

    payload = dict(body.payload or {})
    if kind == "images":
        steps = hibtv_jobs.image_steps(db, project, payload)
        if steps <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有需要出图的资产")
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

    if kind in ("script", "storyboard") and not ep:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请指定集数")

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
    # 运行中的作业按段/按张收敛：已产出物保留（与原型一致）
    return hibtv_jobs.job_payload(job)


# ------------------------------------------------------------------ 资产卡
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
        _log_change(db, project.id, f"asset:{asset.id}", "data", old_raw, merged)
        asset.data = hibtv_jobs.jdumps(merged)
    asset.updated_at = hibtv_jobs.now()
    db.commit()
    return hibtv_jobs.asset_payload(project.id, asset)


@router.post("/projects/{project_id}/assets/{asset_id}/image",
             status_code=status.HTTP_202_ACCEPTED)
def asset_image(project_id: int, asset_id: int, body: HibtvImageIn,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """出图：锚点 / 补齐全部视图 / 指定视图重出 / 接受当前版本。"""
    project = _project(db, project_id, user)
    asset = db.get(HibtvAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    defs = hibtv_jobs.VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    anchor = defs[0][0]

    if body.action == "confirm":
        data = hibtv_jobs.jloads(asset.data, {})
        data["anchorConfirmed"] = True
        asset.data = hibtv_jobs.jdumps(data)
        asset.updated_at = hibtv_jobs.now()
        db.commit()
        return hibtv_jobs.asset_payload(project.id, asset)

    payload: Dict = {"asset_id": asset.id, "kind": asset.kind}
    if body.action == "regen":
        view = body.view or anchor
        payload.update({"mode": "views", "views": [view]})
    elif body.action == "views":
        payload.update({"mode": "views", "views": body.views or [v[0] for v in defs[1:]]})
    elif body.action == "all":
        payload["mode"] = "all"
    else:
        payload["mode"] = "anchor"

    if body.hint.strip():
        data = hibtv_jobs.jloads(asset.data, {})
        hints = data.get("hints") or {}
        for view in (payload.get("views") or [anchor]):
            hints[view] = body.hint.strip()[:400]
        data["hints"] = hints
        asset.data = hibtv_jobs.jdumps(data)

    steps = hibtv_jobs.image_steps(db, project, payload)
    if steps <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有需要出图的视图")
    cost = compute_ad_image_cost(steps)
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"积分不足：本次出图 {steps} 张需 {cost} 积分")
    if cost:
        add_credits(db, user, -cost, "consume", note=f"短剧出图 {steps} 张")
    payload["cost"] = cost
    job = hibtv_jobs.enqueue(db, project.id, "images", payload=payload,
                             ref=asset.code)
    db.commit()
    db.refresh(job)
    return hibtv_jobs.job_payload(job)


@router.post("/projects/{project_id}/assets/{asset_id}/upload")
async def upload_asset(project_id: int, asset_id: int,
                       file: UploadFile = File(...),
                       view: str = Form(default=""),
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """手工上传一张图顶掉某个视图（默认锚点），并让派生视图标记过期。"""
    project = _project(db, project_id, user)
    asset = db.get(HibtvAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    defs = hibtv_jobs.VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    valid = [v[0] for v in defs]
    target = view or valid[0]
    if target not in valid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未知视角")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 JPG / PNG / WebP 图片")
    content = await file.read()
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片不能超过 20MB")

    rel = f"assets/{asset.kind}/{asset.code or asset.id}/{target}{suffix}"
    path = hibtv_jobs.project_dir(project.id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    upload = Upload(user_id=project.user_id, slot="reference",
                    filename=file.filename or path.name, path=str(path),
                    md5="", size=len(content))
    db.add(upload)
    db.flush()

    views = hibtv_jobs.jloads(asset.views, {})
    version = int((views.get(target) or {}).get("version") or 0) + 1
    views[target] = {
        "path": rel, "upload_id": upload.id, "version": version,
        "uploaded": True, "stale": False,
        "at": hibtv_jobs.now().strftime("%Y-%m-%d %H:%M"),
    }
    asset.views = hibtv_jobs.jdumps(views)
    asset.version = max(int(asset.version or 0), version)
    if target == valid[0]:
        hibtv_jobs.mark_derived_stale(asset)
    has_anchor = bool((views.get(valid[0]) or {}).get("path"))
    every = all(bool((views.get(v) or {}).get("path")) for v in valid)
    asset.status = "done" if (has_anchor and every) else ("review" if has_anchor else "empty")
    asset.updated_at = hibtv_jobs.now()
    db.commit()
    return hibtv_jobs.asset_payload(project.id, asset)


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
    payload["refs"] = hibtv_jobs.segment_ref_ids(db, project, seg)
    return payload


@router.post("/projects/{project_id}/segments/{seg_id}/generate",
             status_code=status.HTTP_202_ACCEPTED)
def generate_segment(project_id: int, seg_id: str, body: HibtvSegmentGenerateIn,
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """段级出片：直接建一条 H3 Task（r2v + 锚点参考图，不做云端增强）。"""
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    data = hibtv_jobs.jloads(seg.data, {})
    prompt = (body.prompt or data.get("h3Prompt") or "").strip()
    if not prompt:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "本段还没有 H3 提示词，先跑分镜")
    if body.prompt:
        data["h3Prompt"] = body.prompt.strip()
        seg.data = hibtv_jobs.jdumps(data)

    plan = sum(float(c.get("seconds") or 0) for c in (data.get("cuts") or []))
    seconds = hibtv_jobs.snap_duration(plan or settings.hibtv_seg_max_seconds)
    cost = compute_cost(seconds, "768p")
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED,
                            f"积分不足：本段 {seconds}s 需 {cost} 积分，请先充值")
    ref_ids = hibtv_jobs.segment_ref_ids(db, project, seg)

    task = Task(
        user_id=user.id,
        mode="r2v" if ref_ids else "t2v",
        prompt=prompt,
        aspect_ratio=project.aspect_ratio or "9:16",
        duration=seconds,
        resolution="768p",
        enhance=False,          # h3Prompt 已逐字对齐，不再叠云端增强
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
        "at": hibtv_jobs.now().strftime("%Y-%m-%d %H:%M"),
    })
    seg.versions = hibtv_jobs.jdumps(versions)
    seg.status = "running"
    seg.updated_at = hibtv_jobs.now()
    db.commit()
    return {
        "ok": True, "taskId": task.id, "v": version, "cost": cost,
        "seconds": seconds, "refs": len(ref_ids), "planSeconds": round(plan, 2),
    }


@router.post("/projects/{project_id}/segments/{seg_id}/rebuild")
def rebuild_segment(project_id: int, seg_id: str,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """镜头重修：按当前镜头清单重算这一段 h3Prompt，并同步回分镜文档与段表。

    改过秒数/镜头描述后必须重算，否则对齐指令与切点时刻会与画面对不上。
    """
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    outline_doc = hibtv_jobs.get_doc(db, project.id, "outline")
    outline = hibtv_jobs.jloads(outline_doc.data, {}) if outline_doc else {}
    script_doc = hibtv_jobs.get_doc(db, project.id, "script", seg.ep)
    script_ep = (hibtv_jobs.jloads(script_doc.data, {}).get("episodes") or [{}])[0] \
        if script_doc else {}
    data = hibtv_jobs.jloads(seg.data, {})
    old = data.get("h3Prompt") or ""
    new = hibtv_llm.rebuild_h3_prompt(data, script_ep, outline)
    if new == old:
        return _segment_detail(db, project, seg)

    data["h3Prompt"] = new
    seg.data = hibtv_jobs.jdumps(data)
    seg.updated_at = hibtv_jobs.now()

    board = hibtv_jobs.get_doc(db, project.id, "storyboard", seg.ep)
    if board is not None:
        bdata = hibtv_jobs.jloads(board.data, {})
        try:
            hibtv_jobs.set_json_path(
                bdata, f"episodes.0.segments.{seg.idx}.h3Prompt", new)
            hibtv_jobs.save_doc(db, project.id, "storyboard", seg.ep, bdata)
        except (ValueError, KeyError, IndexError):
            pass
    _log_change(db, project.id, f"storyboard#{seg.ep}",
                f"episodes.0.segments.{seg.idx}.h3Prompt", old, new)
    db.commit()
    db.refresh(seg)
    return _segment_detail(db, project, seg)


@router.post("/projects/{project_id}/segments/{seg_id}/choose")
def choose_version(project_id: int, seg_id: str, body: HibtvSegmentChooseIn,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _project(db, project_id, user)
    seg = _segment_or_404(db, project, seg_id)
    versions = hibtv_jobs.jloads(seg.versions, [])
    if not any(int(v.get("v") or 0) == int(body.v) for v in versions):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "版本不存在")
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
    rows = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id, HibtvSegment.ep == ep)
            .order_by(HibtvSegment.idx).all())
    if not rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "本集还没有分镜段，先跑分镜")
    missing = [s.seg_id for s in rows if not (_current_version(s).get("path"))]
    if missing:
        head = "、".join(missing[:5])
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"{len(missing)} 段还没有成片（{head}…），先全部出片再拼接")
    if not hibtv_compose.ffmpeg_bin():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "服务端未安装 ffmpeg，无法拼接")

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
        _resync(db, project, kind)

    change.reverted = True
    db.commit()
    return {"ok": True, "id": change.id, "what": what, "path": change.path}


# ------------------------------------------------------------------ 素材库
@router.get("/projects/{project_id}/library")
def project_library(project_id: int, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """本项目产出的图 / 片（素材库页）：资产视图 + 段成片 + 整集成片。"""
    project = _project(db, project_id, user)
    items: List[Dict] = []
    assets = (db.query(HibtvAsset)
              .filter(HibtvAsset.project_id == project.id, HibtvAsset.hidden.is_(False))
              .order_by(HibtvAsset.kind, HibtvAsset.sort).all())
    for asset in assets:
        views = hibtv_jobs.jloads(asset.views, {})
        for view, meta in views.items():
            rel = (meta or {}).get("path") or ""
            if not rel:
                continue
            items.append({
                "id": f"a{asset.id}-{view}", "kind": "image", "group": asset.kind,
                "code": asset.code, "name": f"{asset.name} · {view}",
                "url": f"/files/hibtv/{project.id}/{rel}", "version": meta.get("version") or 0,
                "stale": bool(meta.get("stale")), "at": meta.get("at") or "",
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
    return {
        "project": _project_payload(db, project),
        "docs": {
            _doc_key(d): {
                "status": d.status, "version": d.version,
                "gates": hibtv_jobs.jloads(d.gates, {}),
                "data": hibtv_jobs.jloads(d.data, {}),
            }
            for d in docs
        },
        "renders": [{
            "ep": r.ep, "version": r.version,
            "url": f"/files/hibtv/{project.id}/{r.path}",
            "durationSec": float(r.duration_sec or 0),
            "cutlist": hibtv_jobs.jloads(r.cutlist, []),
        } for r in renders],
    }

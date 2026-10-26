"""剧集工坊 HTTP 端点。

前缀 /api/studio；全部需要登录；项目按 user_id 隔离，非本人一律 404（不泄露存在性）。
业务错误统一 HTTPException(status, detail=中文)，前端直接展示 detail。

M0 做「导入 → 看文档 → 跑门 → 出设定图」这条闭环；M2 接「分段 → 切表」，
出段视频与成片在 M3。出图只写队列（Task mode=image + StudioJob），实跑在 worker 的设定图循环里。
"""
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Task, User
from ..services import studio_image
from ..services.billing import compute_cost, compute_studio_image_cost
from . import clips as clipmod
from . import docs as docmod
from . import gates as gatemod
from . import generator as genmod
from . import keyframes as kfmod
from . import prompts as promptmod
from . import render as rendermod
from .importer import episode_rows, episode_stats, import_project
from .intake import build_all
from .models import (
    StudioAsset,
    StudioAssetView,
    StudioEpisode,
    StudioJob,
    StudioKeyframe,
    StudioProject,
    StudioSegmentClip,
)
from .schemas import (
    AssetImageIn,
    DocumentIn,
    GatesOut,
    ImportIn,
    JobOut,
    ProjectCreateIn,
    ProjectPatchIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/studio", tags=["studio"])

GATE_KINDS = {"outline", "script", "cast", "art", "staging", "storyboard"}


def studio_user(user: User = Depends(get_current_user)) -> User:
    if not settings.studio_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "剧集工坊未开启（STUDIO_ENABLED=1）")
    return user


def _project(db: Session, project_id: int, user: User) -> StudioProject:
    return docmod.project_or_404(db, project_id, user.id)


def _episode(db: Session, project: StudioProject, ep_no: int) -> StudioEpisode:
    row = docmod.episode_ref(db, project.id, ep_no)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"第 {ep_no} 集不存在")
    return row


def _iso(value) -> str:
    return value.isoformat(timespec="seconds") if value else ""


# ---------------------------------------------------------------- 序列化

def project_out(p: StudioProject) -> dict:
    try:
        look = json.loads(p.look or "{}")
    except ValueError:
        look = {}
    return {
        "id": p.id,
        "title": p.title,
        "source_name": p.source_name,
        "status": p.status,
        "look": look,
        "created_at": _iso(p.created_at),
        "updated_at": _iso(p.updated_at),
    }


def episode_out(e: StudioEpisode) -> dict:
    return {
        "id": e.id,
        "ep_no": e.ep_no,
        "name": e.name,
        "status": e.status,
        "updated_at": _iso(e.updated_at),
    }


def asset_out(a: StudioAsset, views: list) -> dict:
    try:
        data = json.loads(a.data or "{}")
    except ValueError:
        data = {}
    return {
        "id": a.id,
        "kind": a.kind,
        "ref_id": a.ref_id,
        "name": a.name,
        "status": a.status,
        "data": data,
        "views": [
            {
                "id": v.id,
                "view": v.view,
                "version": v.version,
                "path": v.path,
                "url": f"/files/studio/{v.id}",
                "sha256": v.sha256,
                "confirmed": v.confirmed,
            }
            for v in views
        ],
    }


def job_out(j: StudioJob) -> dict:
    return {
        "id": j.id,
        "kind": j.kind,
        "target_type": j.target_type,
        "target_key": j.target_key,
        "task_id": j.task_id,
        "status": j.status,
        "cost": j.cost,
        "error": j.error,
        "created_at": _iso(j.created_at),
    }


# ---------------------------------------------------------------- 项目

@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    project = StudioProject(
        user_id=user.id,
        title=payload.title.strip(),
        look=docmod.dumps(payload.look or {}),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project_out(project)


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(studio_user)):
    rows = (
        db.query(StudioProject)
        .filter(StudioProject.user_id == user.id)
        .order_by(StudioProject.id.desc())
        .all()
    )
    return {"items": [project_out(p) for p in rows]}


@router.get("/projects/{project_id}")
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    project = _project(db, project_id, user)
    episodes = episode_rows(db, project.id)
    return {
        "project": project_out(project),
        "episodes": [episode_out(e) for e in episodes],
        "counts": {
            "episodes": len(episodes),
            "assets": db.query(StudioAsset).filter(StudioAsset.project_id == project.id).count(),
        },
    }


@router.patch("/projects/{project_id}")
def patch_project(
    project_id: int,
    payload: ProjectPatchIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    project = _project(db, project_id, user)
    if payload.title is not None:
        project.title = payload.title.strip()
    if payload.look is not None:
        project.look = docmod.dumps(payload.look)
    db.commit()
    db.refresh(project)
    return project_out(project)


@router.post("/projects/{project_id}/import")
def import_docs(
    project_id: int,
    payload: ImportIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """解析两份 Markdown 并落库。只给一份时按已有原文补齐另一份。"""
    project = _project(db, project_id, user)
    script_md = payload.script_md or ""
    art_md = payload.art_md or ""
    if not script_md and not art_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "script_md 与 art_md 至少给一份")
    if not art_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少美术设定集（art_md）；M0 需要两份一起导入")
    if not script_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少剧本（script_md）")
    try:
        stats = import_project(db, project, script_md, art_md, payload.force)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    logger.info("studio import project=%s %s", project.id, stats)
    return {"project": project_out(project), "stats": stats}


@router.post("/projects/{project_id}/preview")
def preview_import(
    project_id: int,
    payload: ImportIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """不落库，先看解析结果与四类门的结论（导入确认页用）。"""
    _project(db, project_id, user)
    if not payload.script_md or not payload.art_md:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "预览需要 script_md 与 art_md")
    parsed = build_all(payload.script_md, payload.art_md, "")
    checks = {}
    for kind in ("outline", "script", "cast", "art"):
        out = gatemod.run_bridge(kind, parsed[kind], parsed)
        checks[kind] = gatemod.merge(kind, out)
    return {
        "stats": {
            "episodes": len(parsed["script"]["episodes"]),
            "characters": len(parsed["cast"]["characters"]),
            "scenes": len(parsed["art"]["scenes"]),
            "props": len(parsed["art"]["props"]),
        },
        "checks": checks,
    }


@router.get("/projects/{project_id}/map")
def project_map(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """80 集地图：每集几场、多少秒、什么状态。"""
    project = _project(db, project_id, user)
    stats = episode_stats(db, project.id)
    items = []
    for e in episode_rows(db, project.id):
        row = stats.get(e.ep_no, {})
        items.append({**episode_out(e), **row})
    return {"items": items}


# ---------------------------------------------------------------- 系列级文档

def _bad_kind(kind: str) -> None:
    if kind not in GATE_KINDS and kind != "series":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知文档类型：{kind}")


@router.get("/projects/{project_id}/docs/{kind}")
def get_document(
    project_id: int,
    kind: str,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    _bad_kind(kind)
    project = _project(db, project_id, user)
    row = docmod.get_document_row(db, project.id, kind)
    return {
        "kind": kind,
        "data": docmod.get_doc(db, project.id, kind),
        "updated_at": _iso(row.updated_at) if row else "",
    }


@router.put("/projects/{project_id}/docs/{kind}")
def put_document(
    project_id: int,
    kind: str,
    payload: DocumentIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    _bad_kind(kind)
    project = _project(db, project_id, user)
    row = docmod.get_document_row(db, project.id, kind)
    if payload.base_updated_at and row and _iso(row.updated_at) != payload.base_updated_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "文档已被修改，请刷新后重试")
    row = docmod.put_doc(db, project.id, kind, payload.data)
    return {"kind": kind, "data": payload.data, "updated_at": _iso(row.updated_at)}


@router.post("/projects/{project_id}/gates/{kind}", response_model=GatesOut)
def run_project_gates(
    project_id: int,
    kind: str,
    payload: Optional[DocumentIn] = None,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    if kind not in GATE_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知门类型：{kind}")
    project = _project(db, project_id, user)
    data = payload.data if payload else None
    return gatemod.check(db, project, kind, data)


# ---------------------------------------------------------------- 分集

@router.get("/projects/{project_id}/episodes/{ep_no}")
def get_episode(
    project_id: int,
    ep_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    script = docmod.get_doc(db, project.id, "script", episode.id)
    staging = docmod.get_doc(db, project.id, "staging", episode.id)
    storyboard = docmod.get_doc(db, project.id, "storyboard", episode.id)
    return {
        "episode": episode_out(episode),
        "script": script,
        "docs": {"staging": staging, "storyboard": storyboard},
        # 段卡与节奏带的数据来源；前端不用自己重算
        "summary": genmod.summary(storyboard),
    }


@router.post("/projects/{project_id}/episodes/{ep_no}/generate/{kind}")
def generate_episode_doc(
    project_id: int,
    ep_no: int,
    kind: str,
    payload: Optional[DocumentIn] = None,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """确定性生成分段（staging）或切表（storyboard）；带 body 则用传入的剧本。"""
    if kind not in genmod.KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"只能生成 {' / '.join(genmod.KINDS)}")
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    data = payload.data if payload is not None else None
    try:
        out = genmod.generate(db, project, episode, kind, data=data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except RuntimeError as exc:
        logger.warning("studio generate %s EP%s 失败：%s", kind, ep_no, exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {
        "episode": episode_out(episode),
        "doc": out["doc"],
        "summary": genmod.summary(out["doc"], kind),
        "gates": out["gates"],
    }


@router.post("/projects/{project_id}/episodes/{ep_no}/segments/{segment_id}/keyframes")
def queue_segment_keyframes(
    project_id: int,
    ep_no: int,
    segment_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """给一段的每一切排一张关键帧；已就绪的默认跳过，force 全部重出。

    参考图按景别挑（远景挂全身、特写挂正脸），角色没出设定图就拦下不排空任务；
    空场切允许纯文生图。只写队列，实际出图在 worker 的设定图循环里。
    """
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    try:
        plan = kfmod.segment_plan(db, project, episode, segment_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    queued: list = []
    skipped = 0
    blockers: list = []
    for item in plan["cuts"]:
        row = (
            db.query(StudioKeyframe)
            .filter(
                StudioKeyframe.episode_id == episode.id,
                StudioKeyframe.segment_id == segment_id,
                StudioKeyframe.cut_index == item["cut_index"],
            )
            .one_or_none()
        )
        if row is not None and row.status in ("queued", "ready") and not force:
            skipped += 1
            continue
        if item["missing"]:
            blockers.append(f"切 {item['cut_index']}：{'；'.join(item['missing'])}")
            continue

        task = Task(
            user_id=user.id,
            mode="image",
            prompt=item["prompt"],
            image_prompt=item["prompt"],
            duration=0,
            resolution="768p",
            enhance=False,
            aspect_ratio=kfmod.RATIO,
            scene="studio",
            segments=docmod.dumps({
                "target": "keyframe",
                "project_id": project.id,
                "episode_id": episode.id,
                "segment_id": segment_id,
                "cut_index": item["cut_index"],
                "prompt": item["prompt"],
                "refs": item["refs"],
                "ratio": kfmod.RATIO,
                "out_path": item["path"],
            }),
            status="queued",
        )
        db.add(task)
        db.flush()

        if row is None:
            row = StudioKeyframe(
                episode_id=episode.id, segment_id=segment_id, cut_index=item["cut_index"]
            )
            db.add(row)
        row.status = "queued"
        row.prompt = item["prompt"]
        row.refs = docmod.dumps(item["refs"])

        job = StudioJob(
            project_id=project.id,
            episode_id=episode.id,
            kind="image",
            target_type="keyframe",
            target_key=f"{segment_id}:{item['cut_index']}",
            task_id=task.id,
            status="queued",
            cost=compute_studio_image_cost(1),
            payload=docmod.dumps({"refs": item["refs"], "ratio": kfmod.RATIO}),
        )
        db.add(job)
        queued.append(job)

    db.commit()
    for job in queued:
        db.refresh(job)
    logger.info(
        "studio keyframes queued project=%s ep=%s seg=%s queued=%s skipped=%s blocked=%s",
        project.id, ep_no, segment_id, len(queued), skipped, len(blockers),
    )
    return {
        "queued": len(queued),
        "skipped": skipped,
        "blockers": blockers,
        "items": [job_out(j) for j in queued],
    }


@router.get("/projects/{project_id}/episodes/{ep_no}/keyframes")
def list_episode_keyframes(
    project_id: int,
    ep_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """该集关键帧清单：前端按 (segment_id, cut_index) 挂到切行上。"""
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    rows = (
        db.query(StudioKeyframe)
        .filter(StudioKeyframe.episode_id == episode.id)
        .order_by(StudioKeyframe.segment_id, StudioKeyframe.cut_index)
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "segment_id": r.segment_id,
                "cut_index": r.cut_index,
                "status": r.status,
                "url": f"/files/studio/kf/{r.id}" if r.status == "ready" else "",
                "sha256": r.sha256,
            }
            for r in rows
        ]
    }


@router.post("/projects/{project_id}/episodes/{ep_no}/segments/{segment_id}/video")
def queue_segment_video(
    project_id: int,
    ep_no: int,
    segment_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """一段一次 r2v：关键帧齐了才能排。已就绪的默认跳过，force 重出。"""
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    try:
        plan = clipmod.plan(db, user, project, episode, segment_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    clip = clipmod.clip_of(db, episode.id, segment_id)
    if clip is not None:
        clipmod.sync_clip(db, clip)
        # 在跑的不能重复排；出好的要 force 才重出（重出会重新计费）
        if clip.status in ("queued", "running") or (clip.status == "ready" and not force):
            return {
                "queued": 0,
                "skipped": 1,
                "blockers": [],
                "items": [clipmod.out_of(clip, plan["segment"])],
            }

    blockers = list(plan["missing"])
    if not plan["prompt"]:
        blockers.append("这一段没有 H3 提示词，先重跑切表")
    if blockers:
        return {"queued": 0, "skipped": 0, "blockers": blockers, "items": []}

    duration = int(plan["duration"])
    cost = compute_cost(duration, "768p")
    if user.credits < cost:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"积分不足，本段生成需 {cost} 积分（{duration}s/768p），请先充值",
        )

    db.flush()  # 关键帧对应的 Upload 行要先拿到 id
    ref_ids = [int(u.id) for u in plan["uploads"]]
    task = Task(
        user_id=user.id,
        mode="r2v",
        prompt=plan["prompt"],
        aspect_ratio="9:16",
        duration=duration,
        resolution="768p",
        enhance=False,          # h3Prompt 已是最终稿，不再二次改写
        scene="studio",
        ref_image_ids=docmod.dumps(ref_ids),
        status="queued",
    )
    db.add(task)
    db.flush()

    if clip is None:
        clip = StudioSegmentClip(episode_id=episode.id, segment_id=segment_id)
        db.add(clip)
    clip.task_id = task.id
    clip.status = "queued"
    clip.path = ""
    clip.error = ""
    clip.duration_sec = duration
    clip.prompt = plan["prompt"]

    job = StudioJob(
        project_id=project.id,
        episode_id=episode.id,
        kind="video",
        target_type="segment",
        target_key=segment_id,
        task_id=task.id,
        status="queued",
        cost=cost,
        payload=docmod.dumps(
            {"duration": duration, "seconds": plan["seconds"], "refs": ref_ids}
        ),
    )
    db.add(job)
    db.commit()
    db.refresh(clip)
    logger.info(
        "studio clip queued project=%s ep=%s seg=%s duration=%ss refs=%s cost=%s",
        project.id, ep_no, segment_id, duration, len(ref_ids), cost,
    )
    return {
        "queued": 1,
        "skipped": 0,
        "blockers": [],
        "items": [clipmod.out_of(clip, plan["segment"])],
    }


@router.get("/projects/{project_id}/episodes/{ep_no}/clips")
def list_episode_clips(
    project_id: int,
    ep_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """该集段视频清单。状态按任务实时映射，读取时就地回写。"""
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    rows = (
        db.query(StudioSegmentClip)
        .filter(StudioSegmentClip.episode_id == episode.id)
        .order_by(StudioSegmentClip.segment_id)
        .all()
    )
    changed = False
    for row in rows:
        before = (row.status, row.path, row.error)
        clipmod.sync_clip(db, row)
        if (row.status, row.path, row.error) != before:
            changed = True
    if changed:
        db.commit()
    board = docmod.get_doc(db, project.id, "storyboard", episode.id) or {}
    segs = clipmod.segments_of(board, episode.ep_no)
    return {"items": [clipmod.out_of(r, segs.get(r.segment_id)) for r in rows]}


@router.post("/projects/{project_id}/episodes/{ep_no}/render")
def render_episode(
    project_id: int,
    ep_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """全部段出片后拼成片；缺段直接拦，不给半成品。"""
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    # 任务可能已完成但还没人读过：先同步一轮再判断能不能拼
    for row in clipmod.clips_for_episode(db, episode.id):
        clipmod.sync_clip(db, row)
    db.commit()
    try:
        row = rendermod.do_render(db, project, episode)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if row.status != "done":
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, row.error or "拼接失败"
        )
    return {"render": rendermod.out_of(row)}


@router.get("/projects/{project_id}/episodes/{ep_no}/render")
def get_episode_render(
    project_id: int,
    ep_no: int,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """当前成片（最新一条）；从没拼过时 render 为 null。"""
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    return {"render": rendermod.out_of(rendermod.latest(db, episode.id))}


@router.put("/projects/{project_id}/episodes/{ep_no}/docs/{kind}")
def put_episode_document(
    project_id: int,
    ep_no: int,
    kind: str,
    payload: DocumentIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    if kind not in {"script", "staging", "storyboard"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知分集文档：{kind}")
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    row = docmod.get_document_row(db, project.id, kind, episode.id)
    if payload.base_updated_at and row and _iso(row.updated_at) != payload.base_updated_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "文档已被修改，请刷新后重试")
    row = docmod.put_doc(db, project.id, kind, payload.data, episode.id)
    return {"kind": kind, "data": payload.data, "updated_at": _iso(row.updated_at)}


@router.post("/projects/{project_id}/episodes/{ep_no}/gates/{kind}", response_model=GatesOut)
def run_episode_gates(
    project_id: int,
    ep_no: int,
    kind: str,
    payload: Optional[DocumentIn] = None,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    if kind not in GATE_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知门类型：{kind}")
    project = _project(db, project_id, user)
    episode = _episode(db, project, ep_no)
    data = payload.data if payload else None
    return gatemod.check(db, project, kind, data, episode)


# ---------------------------------------------------------------- 能力探测

@router.get("/status")
def studio_status(user: User = Depends(get_current_user)):
    """前端开工前问一次：工坊开没开、出图节点通不通、一张多少钱。"""
    enabled = bool(settings.studio_enabled)
    return {
        "enabled": enabled,
        "image": studio_image.status() if enabled else {"available": False},
        "cost_image": compute_studio_image_cost(1),
    }


# ---------------------------------------------------------------- 设定图

def _asset_of(db: Session, project: StudioProject, asset_id: int) -> StudioAsset:
    asset = db.get(StudioAsset, asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资产不存在")
    return asset


def _latest_view(db: Session, asset_id: int, view: str) -> Optional[StudioAssetView]:
    return (
        db.query(StudioAssetView)
        .filter(StudioAssetView.asset_id == asset_id, StudioAssetView.view == view)
        .order_by(StudioAssetView.version.desc())
        .first()
    )


@router.post("/projects/{project_id}/assets/{asset_id}/image", response_model=JobOut)
def request_asset_image(
    project_id: int,
    asset_id: int,
    payload: AssetImageIn,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """排一张设定图。只写队列，不动节点；实际跑图在 worker 的设定图循环里。

    参考图只按视图规则挂（与 shuohao 一致：派生图只参考锚点），不接受前端乱传：
    front-full / sheet 是文生图；其余三视图自动挂最新版 front-full。
    重出时版本号自动 +1，旧版不删（回溯与对比用）。
    """
    project = _project(db, project_id, user)
    asset = _asset_of(db, project, asset_id)
    allowed = promptmod.views_of(asset.kind)
    view = (payload.view or allowed[0]).strip()
    if view not in allowed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"未知视图：{view}（可选：{'/'.join(allowed)}）"
        )

    try:
        data = json.loads(asset.data or "{}")
    except ValueError:
        data = {}
    spec = promptmod.build(asset.kind, data, view)
    prompt = (payload.prompt or spec["prompt"]).strip()
    if not prompt:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{asset.name} 缺出图提示词，请先在资产里补 image.prompt"
        )
    negative = (payload.negative or spec["negative"]).strip()

    ref_paths = []
    for ref_view in spec["refs"]:
        anchor = _latest_view(db, asset.id, ref_view)
        if anchor is None or not Path(anchor.path).exists():
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"先出并确认{ref_view}（锚点），再出 {view}"
            )
        ref_paths.append(anchor.path)

    last = _latest_view(db, asset.id, view)
    version = int(payload.version or ((last.version + 1) if last else 1))

    task = Task(
        user_id=user.id,
        mode="image",
        prompt=prompt,
        image_prompt=prompt,
        duration=0,
        resolution="768p",
        enhance=False,
        aspect_ratio=spec["ratio"],
        scene="studio",
        segments=docmod.dumps({
            "project_id": project.id,
            "asset_id": asset.id,
            "ref_id": asset.ref_id,
            "view": view,
            "version": version,
            "prompt": prompt,
            "negative": negative,
            "refs": ref_paths,
            "seed": payload.seed,
        }),
        status="queued",
    )
    db.add(task)
    db.flush()

    job = StudioJob(
        project_id=project.id,
        episode_id=0,
        kind="image",
        target_type="asset",
        target_key=f"{asset.ref_id}:{view}:v{version}",
        task_id=task.id,
        status="queued",
        cost=compute_studio_image_cost(1),
        payload=docmod.dumps({"refs": ref_paths, "ratio": spec["ratio"], "label": spec["label"]}),
    )
    db.add(job)
    asset.status = "imaging"
    db.commit()
    db.refresh(job)
    logger.info(
        "studio image queued project=%s asset=%s %s v%s task=%s",
        project.id, asset.ref_id, view, version, task.id,
    )
    return job_out(job)


@router.post("/projects/{project_id}/assets/{asset_id}/views/{view}/confirm")
def confirm_asset_view(
    project_id: int,
    asset_id: int,
    view: str,
    version: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """人工确认一张设定图（锚点未确认时前端应拦住派生图下单）。"""
    project = _project(db, project_id, user)
    asset = _asset_of(db, project, asset_id)
    if view not in promptmod.views_of(asset.kind):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知视图：{view}")

    row = (
        db.query(StudioAssetView)
        .filter(
            StudioAssetView.asset_id == asset.id,
            StudioAssetView.view == view,
            StudioAssetView.version == version,
        )
        .one_or_none()
        if version
        else _latest_view(db, asset.id, view)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{view} 还没有出图")

    row.confirmed = True
    views = (
        db.query(StudioAssetView)
        .filter(StudioAssetView.asset_id == asset.id)
        .all()
    )
    gates = gatemod.asset_gates(
        [
            {
                "view": v.view,
                "version": v.version,
                "confirmed": v.confirmed,
                "sha256": v.sha256,
            }
            for v in views
        ],
        asset.kind,
    )
    asset.status = "ready" if all(g["ok"] for g in gates) else "imaging"
    db.commit()
    db.refresh(asset)
    views = (
        db.query(StudioAssetView)
        .filter(StudioAssetView.asset_id == asset.id)
        .order_by(StudioAssetView.view, StudioAssetView.version)
        .all()
    )
    return {"asset": asset_out(asset, views), "gates": gates}


@router.get("/projects/{project_id}/jobs")
def list_jobs(
    project_id: int,
    kind: Optional[str] = None,
    target_key: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """任务列表。前端轮询用：拿到 done 后重拉 assets。"""
    project = _project(db, project_id, user)
    query = db.query(StudioJob).filter(StudioJob.project_id == project.id)
    if kind:
        query = query.filter(StudioJob.kind == kind)
    if target_key:
        query = query.filter(StudioJob.target_key == target_key)
    rows = query.order_by(StudioJob.id.desc()).limit(max(1, min(limit, 200))).all()

    # 把 task 的真实状态一并带上，避免前端再查一次 /api/tasks
    task_ids = [j.task_id for j in rows if j.task_id]
    tasks = {}
    if task_ids:
        for t in db.query(Task).filter(Task.id.in_(task_ids)).all():
            tasks[t.id] = t
    items = []
    for j in rows:
        item = job_out(j)
        t = tasks.get(j.task_id) if j.task_id else None
        item["task_status"] = t.status if t else ""
        item["task_error"] = (t.error or "") if t else ""
        item["result"] = (t.video_path or "") if t else ""
        items.append(item)
    return {"items": items}


# ---------------------------------------------------------------- 资产

@router.get("/projects/{project_id}/assets")
def list_assets(
    project_id: int,
    kind: Optional[str] = None,
    used_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(studio_user),
):
    """资产清单。used_only=true 只返回 EP01 用到的（M0 只给第一集出图）。"""
    project = _project(db, project_id, user)
    query = db.query(StudioAsset).filter(StudioAsset.project_id == project.id)
    if kind:
        query = query.filter(StudioAsset.kind == kind)
    rows = query.order_by(StudioAsset.kind, StudioAsset.ref_id).all()

    if used_only:
        script = docmod.get_doc(db, project.id, "script", _episode(db, project, 1).id)
        eps = script.get("episodes") or []
        need = {"character": set(), "scene": set(), "prop": set()}
        for sc in (eps[0].get("scenes") if eps else []) or []:
            need["scene"].add(sc.get("sceneId"))
            need["character"].update(sc.get("characters") or [])
            need["prop"].update(sc.get("props") or [])
        rows = [
            a for a in rows
            if a.ref_id in need.get(a.kind, set()) or (a.kind == "character" and a.ref_id in need["character"])
        ]

    ids = [a.id for a in rows]
    views: dict = {}
    if ids:
        for v in (
            db.query(StudioAssetView)
            .filter(StudioAssetView.asset_id.in_(ids))
            .order_by(StudioAssetView.asset_id, StudioAssetView.view, StudioAssetView.version)
            .all()
        ):
            views.setdefault(v.asset_id, []).append(v)
    return {"items": [asset_out(a, views.get(a.id, [])) for a in rows]}

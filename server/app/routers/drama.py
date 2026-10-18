"""短剧 HTTP 端点：立项 / 剧本 / 角色场景 / 分镜 / 就绪 / 生成 / 审片 / 交付。

约定：
- 前缀 /api/drama；全部需要登录；project.user_id 或 admin 才可见，否则 404（不泄露存在性）；
- 业务错误统一 HTTPException(status, detail=中文可读文本)，前端直接展示 detail；
- 响应形状以《短剧流水线落地设计》§6.3 为准，本文件的序列化函数是唯一出口。
"""
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import decode_token, get_current_user
from ..config import UPLOAD_DIR, settings
from ..database import get_db
from ..models import (
    DramaArtifact, DramaCharacter, DramaEpisode, DramaProject, DramaRun, DramaScene,
    DramaShot, Task, Upload, User,
)
from ..schemas import (
    DramaCharacterIn, DramaCharacterLockIn, DramaCharacterPatchIn, DramaEpisodeIn,
    DramaExtractIn, DramaProjectIn, DramaProjectPatchIn, DramaRejectIn, DramaRenderIn,
    DramaRollbackIn, DramaSceneIn, DramaScenePatchIn, DramaScriptGenerateIn,
    DramaScriptSaveIn, DramaShotIn, DramaShotsSaveIn,
)
from ..services import drama_composer as composer, drama_llm, drama_runner
from ..services.drama_runner import DramaError
from ..services.billing import compute_director_cost
from ..services.pool import pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drama", tags=["drama"])

# 下载端点同时接受 Authorization 头与 ?token= 查询参数（<a>/<video> 无法带头）
_bearer_optional = HTTPBearer(auto_error=False)

_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------- 前置
def drama_user(user: User = Depends(get_current_user)) -> User:
    if not settings.drama_enabled or not settings.director_enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "短剧功能未开启（需同时开启 DRAMA_ENABLED 与 DIRECTOR_ENABLED）",
        )
    return user


def _require_node() -> None:
    if not pool.has_director():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前没有可用的导演台节点")


def _project(db: Session, pid: int, user: User) -> DramaProject:
    project = db.get(DramaProject, pid)
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    return project


def _episode(db: Session, eid: int, user: User) -> DramaEpisode:
    episode = db.get(DramaEpisode, eid)
    if episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "剧集不存在")
    _project(db, episode.project_id, user)
    return episode


def _character(db: Session, cid: int, user: User) -> DramaCharacter:
    char = db.get(DramaCharacter, cid)
    if char is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "角色不存在")
    _project(db, char.project_id, user)
    return char


def _scene(db: Session, sid: int, user: User) -> DramaScene:
    scene = db.get(DramaScene, sid)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "场景不存在")
    _episode(db, scene.episode_id, user)
    return scene


def _shot(db: Session, sid: int, user: User) -> DramaShot:
    shot = db.get(DramaShot, sid)
    if shot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "镜头不存在")
    _episode(db, shot.episode_id, user)
    return shot


def _run(db: Session, rid: int, user: User) -> DramaRun:
    run = db.get(DramaRun, rid)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    _episode(db, run.episode_id, user)
    return run


def _guard_writing(db: Session, project_id: int) -> None:
    """生成中禁止改指纹：项目下有 rendering 集时所有写操作 409。"""
    exists = (
        db.query(DramaEpisode.id)
        .filter(DramaEpisode.project_id == project_id, DramaEpisode.status == "rendering")
        .first()
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "本集正在生成中，请等待完成后再修改")


def _raise(exc: DramaError):
    raise HTTPException(exc.status_code, exc.message)


# ---------------------------------------------------------------- 序列化
def _json(raw, default):
    return composer.load_json(raw, default)


def _upload_url(upload_id: Optional[int]) -> Optional[str]:
    return f"/files/upload/{upload_id}" if upload_id else None


def _artifact_url(artifact: Optional[DramaArtifact]) -> Optional[str]:
    return f"/files/drama/{artifact.id}" if artifact is not None else None


def _serialize_project(project: DramaProject) -> dict:
    return {
        "id": project.id, "user_id": project.user_id, "title": project.title,
        "logline": project.logline, "genre": project.genre,
        "style_bible": project.style_bible, "aspect_ratio": project.aspect_ratio,
        "target_episodes": project.target_episodes, "status": project.status,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _serialize_character(char: DramaCharacter) -> dict:
    return {
        "id": char.id, "project_id": char.project_id, "name": char.name,
        "role": char.role, "gender": char.gender, "age": char.age,
        "appearance": char.appearance, "personality": char.personality,
        "costume": char.costume, "voice": char.voice,
        "card_upload_id": char.card_upload_id,
        "card_url": _upload_url(char.card_upload_id),
        "locked": bool(char.locked), "first_episode_id": char.first_episode_id,
    }


def _serialize_scene(scene: DramaScene) -> dict:
    return {
        "id": scene.id, "episode_id": scene.episode_id, "scene_no": scene.scene_no,
        "name": scene.name, "location": scene.location,
        "time_of_day": scene.time_of_day, "description": scene.description,
        "anchor_upload_id": scene.anchor_upload_id,
        "anchor_url": _upload_url(scene.anchor_upload_id),
    }


def _clips_by_shot(db: Session, episode_id: int) -> dict:
    rows = (
        db.query(DramaArtifact)
        .filter(DramaArtifact.episode_id == episode_id, DramaArtifact.kind == "clip",
                DramaArtifact.is_current.is_(True))
        .order_by(DramaArtifact.id)
        .all()
    )
    return {row.shot_id: row for row in rows}


def _full_cost(db: Session, episode: DramaEpisode) -> int:
    run = (
        db.query(DramaRun)
        .filter(DramaRun.episode_id == episode.id, DramaRun.mode == "full",
                DramaRun.status == "done")
        .order_by(DramaRun.id.desc())
        .first()
    )
    if run is None:
        return 0
    task = db.get(Task, run.task_id)
    return int(task.cost or 0) if task else 0


def _serialize_shot(shot: DramaShot, clip: Optional[DramaArtifact], episode: DramaEpisode) -> dict:
    return {
        "id": shot.id, "shot_no": shot.shot_no, "scene_no": shot.scene_no,
        "beat_id": shot.beat_id, "prompt": shot.prompt,
        "duration_s": shot.duration_s, "shot_size": shot.shot_size,
        "camera": shot.camera, "movement": shot.movement,
        "character_ids": _json(shot.character_ids, []),
        "ref_upload_ids": _json(shot.ref_upload_ids, []),
        "continuity_note": shot.continuity_note, "dialogue": shot.dialogue,
        "audio_note": shot.audio_note, "status": shot.status,
        "version_no": shot.version_no or 0,
        "repair_count": shot.repair_count or 0,
        "repair_cost_total": shot.repair_cost_total or 0,
        "clip_url": _artifact_url(clip),
        "stale": bool(clip and clip.spec_hash and clip.spec_hash != episode.spec_hash),
        "qa": _json(shot.qa, {}) if shot.qa else {},
    }


def _episode_bundle(db: Session, episode: DramaEpisode) -> dict:
    shots = (
        db.query(DramaShot).filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no).all()
    )
    clips = _clips_by_shot(db, episode.id)
    final = drama_runner.current_final(db, episode)
    full_cost = _full_cost(db, episode)
    stats = {
        "shots": len(shots),
        "total_seconds": round(sum(s.duration_s for s in shots), 2),
        "rendered": sum(1 for s in shots if s.status == "rendered"),
        "approved": sum(1 for s in shots if s.status == "approved"),
        "rejected": sum(1 for s in shots if s.status == "rejected"),
        "stale": sum(1 for s in shots if clips.get(s.id) and clips[s.id].spec_hash != episode.spec_hash),
        "repair_cost_total": sum(int(s.repair_cost_total or 0) for s in shots),
        "full_cost": full_cost,
    }
    final_stale = bool(final and final.spec_hash and final.spec_hash != episode.spec_hash)
    return {"shots": shots, "clips": clips, "final": final, "stats": stats,
            "final_stale": final_stale}


def _serialize_episode(db: Session, episode: DramaEpisode, with_shots: bool = True) -> dict:
    bundle = _episode_bundle(db, episode)
    shots = bundle["shots"]
    runs = db.query(DramaRun).filter(DramaRun.episode_id == episode.id).all()
    versions = {
        int(clip.version_no or 0) for clip in bundle["clips"].values() if clip.version_no
    }
    data = {
        "id": episode.id, "project_id": episode.project_id,
        "episode_no": episode.episode_no, "title": episode.title,
        "brief": episode.brief, "status": episode.status,
        "spec_hash": episode.spec_hash,
        "final_duration": episode.final_duration or 0,
        "final_url": _artifact_url(bundle["final"]),
        "final_stale": bundle["final_stale"],
        "last_error": episode.last_error or "",
        "beats": _json(episode.beats, []),
        "script": episode.script or "",
        "stats": bundle["stats"],
        "spent": sum(int(run.cost or 0) for run in runs),
        "run_count": len(runs),
        "version_count": len(versions),
        "shots": [
            _serialize_shot(shot, bundle["clips"].get(shot.id), episode) for shot in shots
        ] if with_shots else [],
        "active_run": None,
        "created_at": episode.created_at.isoformat() if episode.created_at else None,
        "updated_at": episode.updated_at.isoformat() if episode.updated_at else None,
    }
    if episode.active_run_id:
        run = db.get(DramaRun, episode.active_run_id)
        if run is not None:
            data["active_run"] = {"id": run.id, "status": run.status}
    return data


def _phase(run: DramaRun, task: Optional[Task]) -> str:
    task_status = task.status if task else ""
    if task_status in ("queued", "enhancing"):
        return "queued"
    if task_status in ("generating_768p", "upscaling"):
        return "generating"
    if task_status == "done" and run.status in ("queued", "postprocess"):
        return "postprocess"
    return run.status


def _serialize_run(db: Session, run: DramaRun) -> dict:
    task = db.get(Task, run.task_id)
    episode = db.get(DramaEpisode, run.episode_id)
    shots = (
        db.query(DramaShot).filter(DramaShot.episode_id == run.episode_id)
        .order_by(DramaShot.shot_no).all()
    )
    clips = _clips_by_shot(db, run.episode_id)
    master = (
        db.query(DramaArtifact)
        .filter(DramaArtifact.episode_id == run.episode_id, DramaArtifact.kind == "master",
                DramaArtifact.version_no == run.id)
        .order_by(DramaArtifact.id.desc()).first()
    )
    return {
        "id": run.id, "mode": run.mode, "status": run.status,
        "phase": _phase(run, task),
        "task_id": run.task_id,
        "task_status": task.status if task else "",
        "task_error": (task.error if task else "") or "",
        "error": run.error or "",
        "cost": int(task.cost or 0) if task else int(run.cost or 0),
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "shot_ids": _json(run.shot_ids, []),
        "master_url": _artifact_url(master),
        "shots": [{
            "shot_no": shot.shot_no,
            "clip_url": _artifact_url(clips.get(shot.id)),
            "version_no": shot.version_no or 0,
            "status": shot.status,
            "stale": bool(clips.get(shot.id) and episode
                          and clips[shot.id].spec_hash != episode.spec_hash),
        } for shot in shots],
    }


def _reject_if_stale(clip: Optional[DramaArtifact], episode: DramaEpisode, shot_no: int) -> None:
    if clip is None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"第 {shot_no} 镜还没有成片")
    if clip.spec_hash != episode.spec_hash:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "该镜头是旧版（分镜已改动），请重新生成整集后再操作",
        )


# ---------------------------------------------------------------- 状态 / 项目
@router.get("/status")
def drama_status(user: User = Depends(get_current_user)):
    return {
        "enabled": bool(settings.drama_enabled and settings.director_enabled),
        "director_enabled": bool(settings.director_enabled),
        "director_node_available": bool(pool.has_director()),
        "pricing": {"cost_768p_5s": settings.cost_768p_5s, "block_seconds": 5},
        "limits": {
            "max_shots": settings.drama_max_shots,
            "max_seconds": settings.drama_max_episode_seconds,
            "min_shot": settings.drama_min_shot_seconds,
            "max_shot": settings.drama_max_shot_seconds,
        },
    }


@router.post("/projects")
def create_project(body: DramaProjectIn, user: User = Depends(drama_user),
                   db: Session = Depends(get_db)):
    project = DramaProject(
        user_id=user.id, title=body.title.strip(), logline=body.logline.strip(),
        genre=body.genre.strip(), style_bible=body.style_bible.strip(),
        aspect_ratio=body.aspect_ratio, target_episodes=body.target_episodes,
    )
    db.add(project)
    db.flush()
    episode = DramaEpisode(
        project_id=project.id, episode_no=1, title="第 1 集",
        brief=project.logline, status="draft",
    )
    db.add(episode)
    db.commit()
    return {"project": _serialize_project(project), "episode": _serialize_episode(db, episode)}


@router.get("/projects")
def list_projects(user: User = Depends(drama_user), db: Session = Depends(get_db)):
    query = db.query(DramaProject).order_by(DramaProject.id.desc())
    if user.role != "admin":
        query = query.filter(DramaProject.user_id == user.id)
    projects = []
    for project in query.all():
        episodes = (
            db.query(DramaEpisode).filter(DramaEpisode.project_id == project.id)
            .order_by(DramaEpisode.episode_no).all()
        )
        item = _serialize_project(project)
        item["episode_count"] = len(episodes)
        item["episodes"] = [{
            "id": ep.id, "episode_no": ep.episode_no, "title": ep.title,
            "status": ep.status, "final_url": _artifact_url(drama_runner.current_final(db, ep)),
            "final_duration": ep.final_duration or 0,
            "shot_count": db.query(func.count(DramaShot.id))
            .filter(DramaShot.episode_id == ep.id).scalar() or 0,
        } for ep in episodes]
        projects.append(item)
    return {"projects": projects}


@router.get("/projects/{pid}")
def get_project(pid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    project = _project(db, pid, user)
    episodes = (
        db.query(DramaEpisode).filter(DramaEpisode.project_id == pid)
        .order_by(DramaEpisode.episode_no).all()
    )
    characters = (
        db.query(DramaCharacter).filter(DramaCharacter.project_id == pid)
        .order_by(DramaCharacter.id).all()
    )
    return {
        "project": _serialize_project(project),
        "episodes": [_serialize_episode(db, ep) for ep in episodes],
        "characters": [_serialize_character(c) for c in characters],
    }


@router.patch("/projects/{pid}")
def patch_project(pid: int, body: DramaProjectPatchIn, user: User = Depends(drama_user),
                  db: Session = Depends(get_db)):
    project = _project(db, pid, user)
    _guard_writing(db, pid)
    for field in ("title", "logline", "genre", "style_bible", "aspect_ratio", "target_episodes"):
        value = getattr(body, field)
        if value is not None:
            setattr(project, field, value.strip() if isinstance(value, str) else value)
    composer.refresh_project_specs(db, pid)
    db.commit()
    return {"project": _serialize_project(project)}


@router.post("/projects/{pid}/episodes")
def create_episode(pid: int, body: Optional[DramaEpisodeIn] = None,
                   user: User = Depends(drama_user), db: Session = Depends(get_db)):
    project = _project(db, pid, user)
    last = (
        db.query(func.max(DramaEpisode.episode_no))
        .filter(DramaEpisode.project_id == pid).scalar() or 0
    )
    next_no = int(last) + 1
    if next_no > project.target_episodes:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"目标集数为 {project.target_episodes}，请先调整目标集数再新建",
        )
    episode = DramaEpisode(
        project_id=pid, episode_no=next_no,
        title=(body.title if body and body.title else f"第 {next_no} 集"),
        brief=(body.brief if body and body.brief else project.logline),
        status="draft",
    )
    db.add(episode)
    db.commit()
    return {"episode": _serialize_episode(db, episode)}


@router.get("/episodes/{eid}")
def get_episode(eid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    project = db.get(DramaProject, episode.project_id)
    characters = (
        db.query(DramaCharacter).filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id).all()
    )
    scenes = (
        db.query(DramaScene).filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no).all()
    )
    return {
        "episode": _serialize_episode(db, episode),
        "project": _serialize_project(project),
        "characters": [_serialize_character(c) for c in characters],
        "scenes": [_serialize_scene(s) for s in scenes],
    }


# ---------------------------------------------------------------- 剧本
@router.post("/episodes/{eid}/script/generate")
def generate_script(eid: int, body: DramaScriptGenerateIn, user: User = Depends(drama_user),
                    db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _guard_writing(db, episode.project_id)
    beats = _json(episode.beats, [])
    if beats and not body.regenerate:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"已有 {len(beats)} 条节拍，重新生成会覆盖，请确认后重试",
        )
    project = db.get(DramaProject, episode.project_id)
    result = drama_llm.generate_script(
        episode.brief or project.logline, project.logline, project.style_bible,
        body.beat_count, body.target_seconds,
    )
    episode.beats = json.dumps(result["beats"], ensure_ascii=False)
    episode.script = result["script"]
    if episode.status in ("draft", "script_ready"):
        episode.status = "script_ready"
    db.commit()
    return {
        "source": result["source"], "beats": result["beats"],
        "script": result["script"], "status": episode.status,
    }


@router.put("/episodes/{eid}/script")
def save_script(eid: int, body: DramaScriptSaveIn, user: User = Depends(drama_user),
                db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _guard_writing(db, episode.project_id)
    episode.beats = json.dumps(body.beats, ensure_ascii=False)
    episode.script = body.script or episode.script
    if episode.status in ("draft", "script_ready"):
        episode.status = "script_ready"
    db.commit()
    return {"status": episode.status, "beats": body.beats}


# ---------------------------------------------------------------- 角色
@router.post("/projects/{pid}/characters")
def create_character(pid: int, body: DramaCharacterIn, user: User = Depends(drama_user),
                     db: Session = Depends(get_db)):
    _project(db, pid, user)
    _guard_writing(db, pid)
    exists = (
        db.query(DramaCharacter)
        .filter(DramaCharacter.project_id == pid, DramaCharacter.name == body.name.strip())
        .first()
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "同名角色已存在")
    char = DramaCharacter(
        project_id=pid, name=body.name.strip(), role=body.role,
        gender=body.gender, age=body.age, appearance=body.appearance,
        personality=body.personality, costume=body.costume, voice=body.voice,
    )
    db.add(char)
    composer.refresh_project_specs(db, pid)
    db.commit()
    return {"character": _serialize_character(char)}


@router.patch("/characters/{cid}")
def patch_character(cid: int, body: DramaCharacterPatchIn, user: User = Depends(drama_user),
                    db: Session = Depends(get_db)):
    char = _character(db, cid, user)
    _guard_writing(db, char.project_id)
    if body.name and body.name.strip() != char.name:
        clash = (
            db.query(DramaCharacter)
            .filter(DramaCharacter.project_id == char.project_id,
                    DramaCharacter.name == body.name.strip(), DramaCharacter.id != char.id)
            .first()
        )
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, "同名角色已存在")
    for field in ("name", "role", "gender", "age", "appearance", "personality", "costume", "voice"):
        value = getattr(body, field)
        if value is not None:
            setattr(char, field, value.strip() if isinstance(value, str) else value)
    composer.refresh_project_specs(db, char.project_id)
    db.commit()
    return {"character": _serialize_character(char)}


@router.delete("/characters/{cid}")
def delete_character(cid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    char = _character(db, cid, user)
    _guard_writing(db, char.project_id)
    if char.locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "角色已锁卡，无法删除；请先解锁")
    shot_count = 0
    for shot in db.query(DramaShot).join(
        DramaEpisode, DramaEpisode.id == DramaShot.episode_id
    ).filter(DramaEpisode.project_id == char.project_id).all():
        if char.id in (_json(shot.character_ids, []) or []):
            shot_count += 1
    if shot_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"「{char.name}」已被 {shot_count} 个镜头引用，不能删除；请先解锁并修改相关镜头",
        )
    db.delete(char)
    composer.refresh_project_specs(db, char.project_id)
    db.commit()
    return {"ok": True}


def _save_upload(file: UploadFile, content: bytes, user: User, db: Session,
                 filename: str) -> Upload:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        suffix = _IMAGE_TYPES.get(file.content_type or "", "")
    if not suffix:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只支持 jpg/png/webp 图片")
    name = f"drama_{user.id}_{uuid.uuid4().hex[:12]}{suffix}"
    path = UPLOAD_DIR / name
    path.write_bytes(content)
    upload = Upload(
        user_id=user.id, slot="reference", filename=filename or name,
        path=str(path), md5=hashlib.md5(content).hexdigest(), size=len(content),
    )
    db.add(upload)
    db.flush()
    return upload


@router.post("/characters/{cid}/upload")
async def upload_character_card(cid: int, file: UploadFile = File(...),
                                view: str = Form(default="front"),
                                user: User = Depends(drama_user),
                                db: Session = Depends(get_db)):
    char = _character(db, cid, user)
    _guard_writing(db, char.project_id)
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片为空")
    if len(content) > _MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "图片超过 20MB")
    upload = _save_upload(file, content, user, db, file.filename or "")
    char.card_upload_id = upload.id
    try:
        extra = json.loads(char.extra or "{}")
    except (TypeError, ValueError):
        extra = {}
    views = extra.get("views") if isinstance(extra.get("views"), dict) else {}
    views[view or "front"] = upload.id
    extra["views"] = views
    char.extra = json.dumps(extra, ensure_ascii=False)
    composer.refresh_project_specs(db, char.project_id)
    db.commit()
    return {"upload_id": upload.id, "character": _serialize_character(char)}


@router.post("/characters/{cid}/lock")
def lock_character(cid: int, body: DramaCharacterLockIn, user: User = Depends(drama_user),
                   db: Session = Depends(get_db)):
    char = _character(db, cid, user)
    _guard_writing(db, char.project_id)
    if body.locked and not char.card_upload_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先上传定妆图再锁卡")
    char.locked = body.locked
    _maybe_advance_cast(db, char.project_id)
    composer.refresh_project_specs(db, char.project_id)
    db.commit()
    statuses = [
        ep.status for ep in db.query(DramaEpisode)
        .filter(DramaEpisode.project_id == char.project_id).all()
    ]
    return {"locked": char.locked, "character": _serialize_character(char),
            "status": "cast_ready" if "cast_ready" in statuses else (statuses[0] if statuses else "")}


def _maybe_advance_cast(db: Session, project_id: int) -> None:
    chars = db.query(DramaCharacter).filter(DramaCharacter.project_id == project_id).all()
    if not chars:
        return
    all_locked = all(c.locked for c in chars if c.role == "main") and any(
        c.role == "main" for c in chars
    )
    if not all_locked:
        return
    for ep in db.query(DramaEpisode).filter(DramaEpisode.project_id == project_id).all():
        if ep.status in ("script_ready", "cast_ready"):
            ep.status = "cast_ready"


@router.post("/episodes/{eid}/characters/extract")
def extract_characters(eid: int, body: Optional[DramaExtractIn] = None,
                       user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _guard_writing(db, episode.project_id)
    beats = _json(episode.beats, [])
    if not beats:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先生成剧本")
    regenerate = bool(body.regenerate) if body else False
    project = db.get(DramaProject, episode.project_id)
    result = drama_llm.extract_cast(beats, project.style_bible)
    characters = (
        db.query(DramaCharacter).filter(DramaCharacter.project_id == episode.project_id).all()
    )
    by_name = {c.name: c for c in characters}
    for item in result["characters"]:
        name = item["name"]
        char = by_name.get(name)
        if char is None:
            char = DramaCharacter(
                project_id=episode.project_id, first_episode_id=episode.id, name=name,
                role=item["role"], gender=item["gender"], age=item["age"],
                appearance=item["appearance"], personality=item["personality"],
                costume=item["costume"], voice=item["voice"],
            )
            db.add(char)
            by_name[name] = char
        elif regenerate and not char.locked:
            char.role = item["role"] or char.role
            char.gender = item["gender"] or char.gender
            char.age = item["age"] or char.age
            char.appearance = item["appearance"] or char.appearance
            char.personality = item["personality"] or char.personality
            char.costume = item["costume"] or char.costume
            char.voice = item["voice"] or char.voice
    db.flush()

    scenes = (
        db.query(DramaScene).filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no).all()
    )
    by_key = {((s.location or s.name) or f"#{s.scene_no}"): s for s in scenes}
    used_nos = {s.scene_no for s in scenes}
    for item in result["scenes"]:
        key = item.get("location") or item.get("name") or ""
        scene = by_key.get(key)
        if scene is None:
            no = item.get("scene_no") or 1
            while no in used_nos:
                no += 1
            scene = DramaScene(
                episode_id=episode.id, scene_no=no, name=item.get("name") or key,
                location=item.get("location") or "", time_of_day=item.get("time_of_day") or "",
                description=item.get("description") or "",
            )
            db.add(scene)
            used_nos.add(no)
            by_key[key] = scene
        elif regenerate:
            scene.name = item.get("name") or scene.name
            scene.time_of_day = item.get("time_of_day") or scene.time_of_day
            scene.description = item.get("description") or scene.description
    composer.refresh_project_specs(db, episode.project_id)
    db.commit()

    all_chars = (
        db.query(DramaCharacter).filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id).all()
    )
    all_scenes = (
        db.query(DramaScene).filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no).all()
    )
    return {
        "source": result["source"],
        "characters": [_serialize_character(c) for c in all_chars],
        "scenes": [_serialize_scene(s) for s in all_scenes],
    }


# ---------------------------------------------------------------- 场景
@router.post("/episodes/{eid}/scenes/extract")
def extract_scenes(eid: int, body: Optional[DramaExtractIn] = None,
                   user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _guard_writing(db, episode.project_id)
    beats = _json(episode.beats, [])
    if not beats:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先生成剧本")
    project = db.get(DramaProject, episode.project_id)
    result = drama_llm.extract_cast(beats, project.style_bible)
    scenes = (
        db.query(DramaScene).filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no).all()
    )
    by_key = {((s.location or s.name) or f"#{s.scene_no}"): s for s in scenes}
    used_nos = {s.scene_no for s in scenes}
    regenerate = bool(body.regenerate) if body else False
    for item in result["scenes"]:
        key = item.get("location") or item.get("name") or ""
        scene = by_key.get(key)
        if scene is None:
            no = item.get("scene_no") or 1
            while no in used_nos:
                no += 1
            scene = DramaScene(
                episode_id=episode.id, scene_no=no, name=item.get("name") or key,
                location=item.get("location") or "", time_of_day=item.get("time_of_day") or "",
                description=item.get("description") or "",
            )
            db.add(scene)
            used_nos.add(no)
            by_key[key] = scene
        elif regenerate:
            scene.name = item.get("name") or scene.name
            scene.time_of_day = item.get("time_of_day") or scene.time_of_day
            scene.description = item.get("description") or scene.description
    composer.refresh_episode_spec(db, episode)
    db.commit()
    all_scenes = (
        db.query(DramaScene).filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no).all()
    )
    return {"scenes": [_serialize_scene(s) for s in all_scenes]}


@router.post("/episodes/{eid}/scenes")
def create_scene(eid: int, body: DramaSceneIn, user: User = Depends(drama_user),
                 db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _guard_writing(db, episode.project_id)
    last = (
        db.query(func.max(DramaScene.scene_no)).filter(DramaScene.episode_id == eid).scalar() or 0
    )
    scene = DramaScene(
        episode_id=eid, scene_no=int(last) + 1, name=body.name.strip(),
        location=body.location or body.name, time_of_day=body.time_of_day,
        description=body.description,
    )
    db.add(scene)
    composer.refresh_episode_spec(db, episode)
    db.commit()
    return {"scene": _serialize_scene(scene)}


@router.patch("/scenes/{sid}")
def patch_scene(sid: int, body: DramaScenePatchIn, user: User = Depends(drama_user),
                db: Session = Depends(get_db)):
    scene = _scene(db, sid, user)
    episode = db.get(DramaEpisode, scene.episode_id)
    _guard_writing(db, episode.project_id)
    for field in ("name", "location", "time_of_day", "description"):
        value = getattr(body, field)
        if value is not None:
            setattr(scene, field, value.strip() if isinstance(value, str) else value)
    composer.refresh_episode_spec(db, episode)
    db.commit()
    return {"scene": _serialize_scene(scene)}


@router.delete("/scenes/{sid}")
def delete_scene(sid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    scene = _scene(db, sid, user)
    episode = db.get(DramaEpisode, scene.episode_id)
    _guard_writing(db, episode.project_id)
    shots = db.query(DramaShot).filter(DramaShot.episode_id == episode.id).all()
    used = [s for s in shots if s.scene_no == scene.scene_no]
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"「{scene.name}」已被 {len(used)} 个镜头引用，不能删除；请先修改相关镜头",
        )
    db.delete(scene)
    composer.refresh_episode_spec(db, episode)
    db.commit()
    return {"ok": True}


@router.post("/scenes/{sid}/upload")
async def upload_scene_anchor(sid: int, file: UploadFile = File(...),
                              user: User = Depends(drama_user),
                              db: Session = Depends(get_db)):
    scene = _scene(db, sid, user)
    episode = db.get(DramaEpisode, scene.episode_id)
    _guard_writing(db, episode.project_id)
    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片为空")
    if len(content) > _MAX_IMAGE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "图片超过 20MB")
    upload = _save_upload(file, content, user, db, file.filename or "")
    scene.anchor_upload_id = upload.id
    composer.refresh_episode_spec(db, episode)
    db.commit()
    return {"upload_id": upload.id, "scene": _serialize_scene(scene)}


# ---------------------------------------------------------------- 分镜
@router.post("/episodes/{eid}/shots/plan")
def plan_shots(eid: int, body: Optional[dict] = None, user: User = Depends(drama_user),
               db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    beats = _json(episode.beats, [])
    if not beats:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先生成剧本")
    characters = (
        db.query(DramaCharacter).filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id).all()
    )
    scenes = (
        db.query(DramaScene).filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no).all()
    )
    if not characters:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先抽取角色")
    if not scenes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先抽取场景")
    mode = str((body or {}).get("mode") or "replace")
    result = drama_llm.plan_shots(
        beats,
        [{"id": c.id, "name": c.name, "role": c.role} for c in characters],
        [{"scene_no": s.scene_no, "name": s.name, "location": s.location} for s in scenes],
        settings.drama_max_shots,
    )
    drafts = result["shots"]
    if mode == "append":
        existing = db.query(func.count(DramaShot.id)).filter(
            DramaShot.episode_id == episode.id
        ).scalar() or 0
        for draft in drafts:
            draft["shot_no"] += int(existing)
    if len(drafts) > settings.drama_max_shots:
        drafts = drafts[:settings.drama_max_shots]
    return {"source": result["source"], "shots": drafts}


def _shot_payload(body: DramaShotIn) -> dict:
    return {
        "scene_no": body.scene_no, "beat_id": body.beat_id, "prompt": body.prompt,
        "duration_s": body.duration_s, "shot_size": body.shot_size,
        "camera": body.camera, "movement": body.movement,
        "character_ids": json.dumps(sorted(set(body.character_ids))),
        "ref_upload_ids": json.dumps(sorted(set(body.ref_upload_ids))),
        "continuity_note": body.continuity_note, "dialogue": body.dialogue,
        "audio_note": body.audio_note,
    }


@router.put("/episodes/{eid}/shots")
def save_shots(eid: int, body: DramaShotsSaveIn, user: User = Depends(drama_user),
               db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _guard_writing(db, episode.project_id)
    if not body.shots:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "镜头表不能为空")
    if len(body.shots) > settings.drama_max_shots:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"镜头数超过上限 {settings.drama_max_shots}",
        )
    nos = [s.shot_no for s in body.shots]
    if sorted(nos) != list(range(1, len(nos) + 1)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "镜号必须从 1 开始连续")

    existing = {
        s.shot_no: s for s in
        db.query(DramaShot).filter(DramaShot.episode_id == eid).all()
    }
    keep = set(nos)
    for shot_no, shot in existing.items():
        if shot_no not in keep:
            db.query(DramaArtifact).filter(DramaArtifact.shot_id == shot.id).delete()
            db.delete(shot)
    for item in sorted(body.shots, key=lambda s: s.shot_no):
        payload = _shot_payload(item)
        shot = existing.get(item.shot_no)
        if shot is None:
            shot = DramaShot(episode_id=eid, shot_no=item.shot_no, **payload)
            db.add(shot)
        else:
            changed = any(getattr(shot, key) != value for key, value in payload.items())
            for key, value in payload.items():
                setattr(shot, key, value)
            if changed:
                shot.status = "ready"
                shot.qa = ""
    db.flush()
    composer.refresh_episode_spec(db, episode)
    episode.status = "shots_ready"
    db.commit()
    shots = (
        db.query(DramaShot).filter(DramaShot.episode_id == eid)
        .order_by(DramaShot.shot_no).all()
    )
    clips = _clips_by_shot(db, eid)
    return {
        "status": episode.status,
        "shots": [_serialize_shot(s, clips.get(s.id), episode) for s in shots],
    }


@router.get("/episodes/{eid}/checklist")
def checklist(eid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    result = composer.validate_episode(db, episode, user)
    return result


# ---------------------------------------------------------------- 生成
@router.post("/episodes/{eid}/render")
def render_episode(eid: int, body: Optional[DramaRenderIn] = None,
                   user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    _require_node()
    force = bool(body.force) if body else False
    try:
        result = drama_runner.submit_full(db, episode, user, force=force)
    except DramaError as exc:
        _raise(exc)
    run = db.get(DramaRun, result["run_id"])
    return {
        "reused": result["reused"], "run": _serialize_run(db, run),
        "cost": result["cost"],
    }


@router.get("/runs/{rid}")
def get_run(rid: int, background: BackgroundTasks, user: User = Depends(drama_user),
            db: Session = Depends(get_db)):
    run = _run(db, rid, user)
    task = db.get(Task, run.task_id)
    if run.status == "queued" and task is not None and task.status == "done":
        background.add_task(drama_runner.postprocess, run.id)
    return _serialize_run(db, run)


@router.post("/runs/{rid}/cancel")
def cancel_run(rid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    run = _run(db, rid, user)
    try:
        drama_runner.cancel_run(db, run)
    except DramaError as exc:
        _raise(exc)
    return {"run": _serialize_run(db, run)}


@router.post("/runs/{rid}/repair")
def repair_run(rid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    run = _run(db, rid, user)
    try:
        drama_runner.repair_run(run.id)
    except DramaError as exc:
        _raise(exc)
    run = db.get(DramaRun, rid)
    return {"run": _serialize_run(db, run)}


# ---------------------------------------------------------------- 审片
@router.get("/shots/{sid}/artifacts")
def list_shot_artifacts(sid: int, user: User = Depends(drama_user),
                        db: Session = Depends(get_db)):
    """某镜头的历史版本（版本抽屉 + 回滚用），新版本在前。"""
    shot = _shot(db, sid, user)
    episode = db.get(DramaEpisode, shot.episode_id)
    artifacts = (
        db.query(DramaArtifact)
        .filter(DramaArtifact.shot_id == sid, DramaArtifact.kind == "clip")
        .order_by(DramaArtifact.version_no.desc(), DramaArtifact.id.desc())
        .all()
    )
    return {
        "shot_no": shot.shot_no,
        "artifacts": [{
            "id": a.id, "version_no": a.version_no, "url": _artifact_url(a),
            "duration": a.duration, "frames": a.frames,
            "is_current": bool(a.is_current),
            "stale": bool(episode and a.spec_hash != episode.spec_hash),
            "created_at": a.created_at.isoformat() if a.created_at else None,
        } for a in artifacts],
    }


@router.post("/shots/{sid}/approve")
def approve_shot(sid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    shot = _shot(db, sid, user)
    episode = db.get(DramaEpisode, shot.episode_id)
    if episode.status != "review":
        raise HTTPException(status.HTTP_409_CONFLICT, "当前集不在审片阶段")
    if shot.status not in ("rendered", "approved"):
        raise HTTPException(status.HTTP_409_CONFLICT, "该镜头当前不可通过")
    clip = drama_runner.current_clip(db, shot.id)
    _reject_if_stale(clip, episode, shot.shot_no)
    shot.status = "approved"
    db.commit()
    return {"status": shot.status, "shot": _serialize_shot(shot, clip, episode)}


@router.post("/shots/{sid}/reject")
def reject_shot(sid: int, body: DramaRejectIn, user: User = Depends(drama_user),
                db: Session = Depends(get_db)):
    shot = _shot(db, sid, user)
    episode = db.get(DramaEpisode, shot.episode_id)
    if episode.status != "review":
        raise HTTPException(status.HTTP_409_CONFLICT, "当前集不在审片阶段")
    if shot.status not in ("rendered", "approved", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, "该镜头当前不可返修")
    clip = drama_runner.current_clip(db, shot.id)
    _reject_if_stale(clip, episode, shot.shot_no)
    if not body.rerun:
        shot.status = "rejected"
        db.commit()
        return {"shot": _serialize_shot(shot, clip, episode)}
    _require_node()
    try:
        result = drama_runner.submit_patch(db, episode, shot, body.reason, anchor=body.anchor)
    except DramaError as exc:
        _raise(exc)
    run = db.get(DramaRun, result["run_id"])
    shot = db.get(DramaShot, sid)
    return {
        "shot": _serialize_shot(shot, clip, episode),
        "run": _serialize_run(db, run),
        "cost": result["cost"],
    }


def _review_warnings(db: Session, episode: DramaEpisode, bundle: dict) -> list:
    warnings = []
    full_cost = bundle["stats"]["full_cost"] or 0
    if full_cost > 0:
        hot = [
            s for s in bundle["shots"]
            if int(s.repair_cost_total or 0) > 0.4 * full_cost
        ]
        if hot:
            warnings.append({
                "code": "REPAIR_COST_HIGH",
                "message": "已返修成本接近半集，建议整集重跑",
                "shot_nos": [s.shot_no for s in hot],
            })
    if bundle["final_stale"]:
        warnings.append({
            "code": "FINAL_STALE",
            "message": "成片已过旧（分镜或角色卡已改动），请重新生成整集后再交付",
        })
    return warnings


def _seams(bundle: dict) -> list:
    shots = bundle["shots"]
    seams = []
    for left, right in zip(shots, shots[1:]):
        qa = _json(left.qa, {}) if left.qa else {}
        metric = qa.get("out") if isinstance(qa, dict) else None
        if not metric:
            continue
        seams.append({
            "between": [left.shot_no, right.shot_no],
            "ssim": metric.get("ssim"), "d_y": metric.get("d_y"),
            "audio_db": metric.get("audio_db"), "level": metric.get("level") or "unknown",
        })
    return seams


@router.get("/episodes/{eid}/review")
def review_episode(eid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    bundle = _episode_bundle(db, episode)
    sheet = drama_runner.current_sheet(db, episode)
    return {
        "episode": _serialize_episode(db, episode),
        "stats": bundle["stats"],
        "shots": [
            _serialize_shot(shot, bundle["clips"].get(shot.id), episode)
            for shot in bundle["shots"]
        ],
        "seams": _seams(bundle),
        "sheet_url": _artifact_url(sheet) or "",
        "warnings": _review_warnings(db, episode, bundle),
    }


@router.post("/episodes/{eid}/finish")
def finish_episode(eid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    try:
        drama_runner.finish_episode(db, episode)
    except DramaError as exc:
        _raise(exc)
    episode = db.get(DramaEpisode, eid)
    return _serialize_episode(db, episode)


@router.post("/episodes/{eid}/reopen")
def reopen_episode(eid: int, user: User = Depends(drama_user), db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    try:
        drama_runner.reopen_episode(db, episode)
    except DramaError as exc:
        _raise(exc)
    episode = db.get(DramaEpisode, eid)
    return {"episode": _serialize_episode(db, episode)}


@router.post("/episodes/{eid}/rollback")
def rollback_episode(eid: int, body: DramaRollbackIn, user: User = Depends(drama_user),
                     db: Session = Depends(get_db)):
    episode = _episode(db, eid, user)
    try:
        shot = drama_runner.rollback(db, episode, body.artifact_id)
    except DramaError as exc:
        _raise(exc)
    episode = db.get(DramaEpisode, eid)
    payload = {"episode": _serialize_episode(db, episode)}
    if shot is not None:
        bundle = _episode_bundle(db, episode)
        payload["shot"] = _serialize_shot(shot, bundle["clips"].get(shot.id), episode)
    return payload


@router.get("/episodes/{eid}/download")
def download_episode(eid: int, kind: str = Query(default="final"),
                     download: int = Query(default=0, ge=0, le=1),
                     token: Optional[str] = Query(default=None),
                     creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_optional),
                     db: Session = Depends(get_db)):
    raw = token or (creds.credentials if creds is not None else None)
    user_id = decode_token(raw) if raw else None
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问凭证")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    episode = _episode(db, eid, user)
    if kind != "final":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只支持下载成片")
    final = drama_runner.current_final(db, episode)
    if final is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成片不存在")
    suffix = f"?token={raw}" if raw else ""
    if download:
        suffix += ("&" if suffix else "?") + "download=1"
    return RedirectResponse(f"/files/drama/{final.id}{suffix}", status_code=307)

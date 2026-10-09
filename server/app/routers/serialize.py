"""任务序列化：ORM → TaskOut（附带带鉴权的文件地址）。"""
import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import OUTPUT_DIR
from ..models import Task, Upload
from ..schemas import TaskOut

_ADVIDEO_STAGE = {
    "queued_images": "images_queued",
    "generating_images": "images_running",
    "image_ready": "image_ready",
}

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _ref_kind(upload_id: int, db: Optional[Session]) -> str:
    """按参考上传文件的后缀判定类型（image/video/audio）；无 db 或找不到时兜底为 image。"""
    if db is None:
        return "image"
    up = db.get(Upload, upload_id)
    if up is None:
        return "image"
    suffix = Path(up.path).suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    if suffix in _AUDIO_SUFFIXES:
        return "audio"
    return "image"


def _ad_image_paths(task: Task) -> list:
    """候选广告图路径列表（字段缺失/脏数据一律返回空列表）。"""
    try:
        paths = json.loads(getattr(task, "ad_image_paths", "") or "[]")
    except (TypeError, ValueError):
        return []
    return [p for p in paths if isinstance(p, str)]


def _advideo_stage(task: Task) -> str:
    if task.mode != "advideo":
        return ""
    return _ADVIDEO_STAGE.get(task.status, "video")


def serialize_task(task: Task, user_email: Optional[str] = None, db: Optional[Session] = None) -> TaskOut:
    try:
        ref_ids = json.loads(task.ref_image_ids or "[]")
    except (TypeError, ValueError):
        ref_ids = []

    try:
        segments = json.loads(getattr(task, "segments", "") or "[]")
    except (TypeError, ValueError):
        segments = []

    # 参考区按扩展名分类：ref_image_urls 仅图片（保持兼容），新增视频/音频列表
    ref_image_urls: list[str] = []
    ref_video_urls: list[str] = []
    ref_audio_urls: list[str] = []
    for rid in ref_ids:
        url = f"/files/upload/{rid}"
        kind = _ref_kind(rid, db)
        if kind == "video":
            ref_video_urls.append(url)
        elif kind == "audio":
            ref_audio_urls.append(url)
        else:
            ref_image_urls.append(url)

    # 视频地址：优先最高可用分辨率
    video_url: Optional[str] = None
    upscale_urls: dict[str, str] = {}

    if task.status == "done":
        # 检查各分辨率成片是否存在
        for res, suffix in (("4k", "_4k"), ("2k", "_2k"), ("1k", "_1k")):
            path = OUTPUT_DIR / f"{task.id}{suffix}.mp4"
            if path.exists():
                upscale_urls[res] = f"/files/video/{task.id}?resolution={res}"

        # video_url 指向最高可用分辨率
        if (OUTPUT_DIR / f"{task.id}_4k.mp4").exists():
            video_url = f"/files/video/{task.id}?resolution=4k"
        elif (OUTPUT_DIR / f"{task.id}_2k.mp4").exists():
            video_url = f"/files/video/{task.id}?resolution=2k"
        elif (OUTPUT_DIR / f"{task.id}_1k.mp4").exists():
            video_url = f"/files/video/{task.id}?resolution=1k"
        elif task.video_path:
            video_url = f"/files/video/{task.id}"

    return TaskOut(
        id=task.id,
        mode=task.mode,
        prompt=task.prompt,
        enhanced_prompt=task.enhanced_prompt or "",
        aspect_ratio=task.aspect_ratio,
        duration=task.duration,
        resolution=task.resolution,
        enhance=task.enhance,
        scene=task.scene,
        status=task.status,
        error=task.error or "",
        cost=task.cost,
        video_url=video_url,
        upscale_urls=upscale_urls,
        first_image_url=f"/files/upload/{task.first_image_id}" if task.first_image_id else None,
        last_image_url=f"/files/upload/{task.last_image_id}" if task.last_image_id else None,
        ref_image_urls=ref_image_urls,
        ref_video_urls=ref_video_urls,
        ref_audio_urls=ref_audio_urls,
        parent_task_id=task.parent_task_id,
        upscale_target=task.upscale_target,
        worker_url=task.worker_url or "",
        segments=segments,
        stage=_advideo_stage(task),
        ad_image_urls=[
            f"/files/adimage/{task.id}?index={i}" for i in range(len(_ad_image_paths(task)))
        ],
        image_prompt=getattr(task, "image_prompt", "") or "",
        chosen_index=(getattr(task, "chosen_index", -1) if getattr(task, "chosen_index", -1) is not None else -1),
        chosen_image_url=(
            f"/files/adimage/{task.id}?index={task.chosen_index}"
            if (getattr(task, "chosen_index", -1) or -1) >= 0 else None
        ),
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        user_email=user_email,
    )

"""文件下载：成片与上传图片。

<video>/<img> 标签无法携带 Authorization 头，因此这里支持
?token=<JWT> 查询参数鉴权（前端 fileUrl 统一拼接）。
"""
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import decode_token
from ..config import OUTPUT_DIR, settings
from ..database import get_db
from ..models import DramaArtifact, DramaEpisode, DramaProject, Task, Upload, User

router = APIRouter(prefix="/files", tags=["files"])


def _user_from_query_token(token: Optional[str], db: Session) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少访问凭证")
    user_id = decode_token(token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "访问凭证无效或已过期")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


@router.get("/video/{task_id}")
def download_video(
    task_id: int,
    resolution: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    user = _user_from_query_token(token, db)
    task = db.get(Task, task_id)
    if task is None or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "视频不存在")
    if task.status != "done" or not task.video_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "视频尚未生成完成")
    # 按分辨率定位文件
    if resolution in ("1k", "2k", "4k"):
        path = OUTPUT_DIR / f"{task_id}_{resolution}.mp4"
    else:
        path = Path(task.video_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "视频文件已失效")
    suffix = f"_{resolution}" if resolution in ("1k", "2k", "4k") else ""
    return FileResponse(
        path, media_type="video/mp4", filename=f"h3_video_{task.id}{suffix}.mp4",
    )


@router.get("/upload/{upload_id}")
def download_upload(
    upload_id: int,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    user = _user_from_query_token(token, db)
    upload = db.get(Upload, upload_id)
    if upload is None or (upload.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")
    path = Path(upload.path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "图片文件已失效")
    suffix = path.suffix.lower()
    media_type = {
        # 图片
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        # 视频（供 <video> 预览）
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm", ".mkv": "video/x-matroska",
        # 音频（供 <audio> 预览）
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
    }.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media_type)

@router.get("/drama/{artifact_id}")
def download_drama(
    artifact_id: int,
    download: int = Query(default=0, ge=0, le=1),
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """短剧产物：镜头切片 / 成片 / 联系表；鉴权链 artifact → episode → project。"""
    user = _user_from_query_token(token, db)
    if settings.drama_admin_only and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "短剧功能测试中，暂未对外开放")
    artifact = db.get(DramaArtifact, artifact_id)
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "产物不存在")
    episode = db.get(DramaEpisode, artifact.episode_id)
    project = db.get(DramaProject, episode.project_id) if episode is not None else None
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "产物不存在")
    path = Path(artifact.path or "")
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "产物文件已失效")
    suffix = path.suffix.lower()
    media_type = {
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    filename = f"drama_{artifact.kind}_{artifact.id}{suffix}"
    return FileResponse(path, media_type=media_type, filename=filename if download else None)


@router.get("/adimage/{task_id}")
def download_advideo_image(
    task_id: int,
    index: int = Query(default=0, ge=0, le=9),
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """电商广告片：下载第 index 张候选广告图（<img> 预览用，?token= 鉴权）。"""
    user = _user_from_query_token(token, db)
    task = db.get(Task, task_id)
    if task is None or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    try:
        paths = json.loads(getattr(task, "ad_image_paths", "") or "[]")
    except (TypeError, ValueError):
        paths = []
    if not (0 <= index < len(paths)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "广告图不存在")
    path = Path(paths[index])
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "广告图文件已失效")
    return FileResponse(path, media_type="image/png", filename=f"advideo_{task_id}_{index}.png")


@router.get("/studio/render/{render_id}")
def download_studio_render(
    render_id: int,
    download: int = Query(default=0, ge=0, le=1),
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """剧集工坊成片：鉴权链 render → episode → project。"""
    from ..studio.models import StudioEpisode, StudioProject, StudioRender

    user = _user_from_query_token(token, db)
    row = db.get(StudioRender, render_id)
    episode = db.get(StudioEpisode, row.episode_id) if row is not None else None
    project = db.get(StudioProject, episode.project_id) if episode is not None else None
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "成片不存在")
    if row.status != "done" or not row.path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "成片尚未拼接完成")
    path = Path(row.path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "成片文件已失效")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"studio_ep{episode.ep_no:02d}.mp4" if download else None,
    )


@router.get("/studio/clip/{clip_id}")
def download_studio_clip(
    clip_id: int,
    download: int = Query(default=0, ge=0, le=1),
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """剧集工坊段视频：鉴权链 clip → episode → project。"""
    from ..studio.models import StudioEpisode, StudioProject, StudioSegmentClip

    user = _user_from_query_token(token, db)
    clip = db.get(StudioSegmentClip, clip_id)
    episode = db.get(StudioEpisode, clip.episode_id) if clip is not None else None
    project = db.get(StudioProject, episode.project_id) if episode is not None else None
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "段视频不存在")
    if clip.status != "ready" or not clip.path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "段视频尚未生成完成")
    path = Path(clip.path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "段视频文件已失效")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"studio_{clip.segment_id}.mp4" if download else None,
    )


@router.get("/studio/{view_id}")
def download_studio_view(
    view_id: int,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """剧集工坊设定图：<img> 预览用（?token= 鉴权）。

    鉴权链 view → asset → project → user；落盘路径不进前端（前端只拿这个 URL）。
    """
    from ..studio.models import StudioAsset, StudioAssetView, StudioProject

    user = _user_from_query_token(token, db)
    row = db.get(StudioAssetView, view_id)
    asset = db.get(StudioAsset, row.asset_id) if row is not None else None
    project = db.get(StudioProject, asset.project_id) if asset is not None else None
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "设定图不存在")
    path = Path(row.path or "")
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "设定图文件已失效")
    return FileResponse(path, media_type="image/png", filename=f"studio_{row.id}.png")


@router.get("/studio/kf/{keyframe_id}")
def download_studio_keyframe(
    keyframe_id: int,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    """剧集工坊关键帧：<img> 预览用（?token= 鉴权）。

    鉴权链 keyframe → episode → project → user；关键帧比设定图多一跳（集）。
    """
    from ..studio.models import StudioEpisode, StudioKeyframe, StudioProject

    user = _user_from_query_token(token, db)
    row = db.get(StudioKeyframe, keyframe_id)
    episode = db.get(StudioEpisode, row.episode_id) if row is not None else None
    project = db.get(StudioProject, episode.project_id) if episode is not None else None
    if project is None or (project.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "关键帧不存在")
    path = Path(row.path or "")
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "关键帧文件已失效")
    return FileResponse(path, media_type="image/png", filename=f"studio_kf_{row.id}.png")

"""文件下载：成片与上传图片。

<video>/<img> 标签无法携带 Authorization 头，因此这里支持
?token=<JWT> 查询参数鉴权（前端 fileUrl 统一拼接）。
"""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import decode_token
from ..database import get_db
from ..models import Task, Upload, User

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
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    user = _user_from_query_token(token, db)
    task = db.get(Task, task_id)
    if task is None or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "视频不存在")
    if task.status != "done" or not task.video_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "视频尚未生成完成")
    path = Path(task.video_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "视频文件已失效")
    return FileResponse(
        path, media_type="video/mp4", filename=f"h3_video_{task.id}.mp4",
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
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media_type)

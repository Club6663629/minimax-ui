"""媒体上传：首帧 / 尾帧 / 参考（参考区支持图片、视频、音频混传）。"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import UPLOAD_DIR
from ..database import get_db
from ..models import Upload, User
from ..schemas import UploadOut

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# 按 content_type 分类到扩展名；参考区三类均可，首尾帧仅图片
_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_VIDEO_TYPES = {
    "video/mp4": ".mp4", "video/quicktime": ".mov",
    "video/webm": ".webm", "video/x-matroska": ".mkv",
}
_AUDIO_TYPES = {
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg", "audio/flac": ".flac",
}
_TYPES_TO_EXT = {**_IMAGE_TYPES, **_VIDEO_TYPES, **_AUDIO_TYPES}

# 分级大小上限
_MAX_SIZE_IMAGE = 20 * 1024 * 1024    # 图片 20MB
_MAX_SIZE_VIDEO = 200 * 1024 * 1024   # 视频 200MB
_MAX_SIZE_AUDIO = 50 * 1024 * 1024    # 音频 50MB
_SIZE_BY_KIND = {"image": _MAX_SIZE_IMAGE, "video": _MAX_SIZE_VIDEO, "audio": _MAX_SIZE_AUDIO}
_KIND_LABEL = {"image": "图片", "video": "视频", "audio": "音频"}
_SLOTS = {"first", "last", "reference"}


def _classify(content_type: str) -> str:
    """按 content_type 分类媒体种类；不支持则返回空串。"""
    if content_type in _IMAGE_TYPES:
        return "image"
    if content_type in _VIDEO_TYPES:
        return "video"
    if content_type in _AUDIO_TYPES:
        return "audio"
    return ""


@router.post("", response_model=UploadOut)
async def upload_media(
    file: UploadFile = File(...),
    slot: str = Form("reference"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if slot not in _SLOTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无效的上传槽位")
    kind = _classify(file.content_type or "")
    if not kind:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "仅支持 JPG / PNG / WebP 图片、MP4 / MOV / WebM / MKV 视频或 MP3 / WAV / M4A / AAC / OGG / FLAC 音频",
        )
    # 首尾帧槽位仅允许图片
    if slot in ("first", "last") and kind != "image":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "首尾帧仅支持图片")

    content = await file.read()
    max_size = _SIZE_BY_KIND[kind]
    if len(content) > max_size:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{_KIND_LABEL[kind]}不能超过 {max_size // (1024 * 1024)}MB")

    ext = _TYPES_TO_EXT[file.content_type]
    name = f"u{user.id}_{uuid.uuid4().hex[:12]}{ext}"
    path = UPLOAD_DIR / name
    path.write_bytes(content)

    upload = Upload(user_id=user.id, slot=slot, filename=file.filename or name, path=str(path))
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return UploadOut(id=upload.id, slot=upload.slot, filename=upload.filename, url=f"/files/upload/{upload.id}")

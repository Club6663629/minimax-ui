"""图片上传：首帧 / 尾帧 / 参考图。"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import UPLOAD_DIR
from ..database import get_db
from ..models import Upload, User
from ..schemas import UploadOut

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
_MAX_SIZE = 20 * 1024 * 1024  # 20MB
_SLOTS = {"first", "last", "reference"}


@router.post("", response_model=UploadOut)
async def upload_image(
    file: UploadFile = File(...),
    slot: str = Form("reference"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if slot not in _SLOTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无效的上传槽位")
    if file.content_type not in _ALLOWED_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅支持 JPG / PNG / WebP 图片")

    content = await file.read()
    if len(content) > _MAX_SIZE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片不能超过 20MB")

    ext = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    }[file.content_type]
    name = f"u{user.id}_{uuid.uuid4().hex[:12]}{ext}"
    path = UPLOAD_DIR / name
    path.write_bytes(content)

    upload = Upload(user_id=user.id, slot=slot, filename=file.filename or name, path=str(path))
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return UploadOut(id=upload.id, slot=upload.slot, filename=upload.filename, url=f"/files/upload/{upload.id}")

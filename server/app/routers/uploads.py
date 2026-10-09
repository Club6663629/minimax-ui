"""媒体上传：首帧 / 尾帧 / 参考（参考区支持图片、视频、音频混传）及资产列表。

资产管理增强（2026-09-28）：
- 每个素材记录内容 md5 指纹与字节大小（上传时计算，历史记录惰性补算）；
- 上传时同槽位同 md5 直接复用既有记录，避免重复入库；
- 提供重复分组预览与一键去重接口（同内容保留最新一条，被任务引用的记录保护不删）。
"""
import hashlib
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import UPLOAD_DIR
from ..database import get_db
from ..models import Task, Upload, User
from ..schemas import (
    DedupGroupOut,
    DedupPreviewOut,
    DedupResultOut,
    UploadListItemOut,
    UploadOut,
)

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


def _kind_by_path(path: str) -> str:
    """按存储文件后缀判定媒体种类（未知按图片计）。"""
    suffix = Path(path).suffix.lower()
    if suffix in _VIDEO_TYPES.values():
        return "video"
    if suffix in _AUDIO_TYPES.values():
        return "audio"
    return "image"


def _md5_file(path: Path) -> str:
    """流式计算文件内容 md5；文件缺失/不可读时返回空串。"""
    digest = hashlib.md5()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _backfill_fingerprints(rows: list[Upload], db: Session) -> None:
    """为历史素材惰性补算 md5 / 大小（只处理缺失项，最多提交一次）。"""
    changed = False
    for u in rows:
        if u.md5 and u.size:
            continue
        path = Path(u.path)
        if not path.exists():
            continue
        digest = _md5_file(path)
        if not digest:
            continue
        u.md5 = digest
        try:
            u.size = path.stat().st_size
        except OSError:
            u.size = 0
        changed = True
    if changed:
        db.commit()


def _referenced_upload_ids(db: Session, user_id: int) -> set[int]:
    """收集被历史任务引用的素材 id（首/尾帧、参考列表、导演台分段），去重时保护不删。"""
    used: set[int] = set()
    rows = (
        db.query(Task.first_image_id, Task.last_image_id, Task.ref_image_ids, Task.segments)
        .filter(Task.user_id == user_id)
        .all()
    )

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "ref_image_ids" and isinstance(value, list):
                    for item in value:
                        try:
                            used.add(int(item))
                        except (TypeError, ValueError):
                            continue
                else:
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for first_id, last_id, ref_json, seg_json in rows:
        for value in (first_id, last_id):
            if value:
                used.add(int(value))
        for raw in (ref_json, seg_json):
            if not raw:
                continue
            try:
                _walk(json.loads(raw))
            except (TypeError, ValueError):
                continue
    return used


def _dup_groups(rows: list[Upload]) -> dict[str, list[Upload]]:
    """按 md5 聚合（rows 需按 id 倒序，组内首个即最新需保留的一条）。"""
    groups: dict[str, list[Upload]] = {}
    for u in rows:
        if u.md5:
            groups.setdefault(u.md5, []).append(u)
    return {k: v for k, v in groups.items() if len(v) > 1}


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

    digest = hashlib.md5(content).hexdigest()
    # 先补齐历史指纹，再判定是否已有同内容素材（同槽位直接复用，避免重复入库）
    history = db.query(Upload).filter(Upload.user_id == user.id).order_by(Upload.id.desc()).all()
    _backfill_fingerprints(history, db)
    for old in history:
        if old.slot == slot and old.md5 == digest and Path(old.path).exists():
            return UploadOut(
                id=old.id,
                slot=old.slot,
                filename=old.filename,
                url=f"/files/upload/{old.id}",
                md5=digest,
                size=old.size or len(content),
                duplicate=True,
            )

    ext = _TYPES_TO_EXT[file.content_type]
    name = f"u{user.id}_{uuid.uuid4().hex[:12]}{ext}"
    path = UPLOAD_DIR / name
    path.write_bytes(content)

    upload = Upload(
        user_id=user.id,
        slot=slot,
        filename=file.filename or name,
        path=str(path),
        md5=digest,
        size=len(content),
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return UploadOut(
        id=upload.id,
        slot=upload.slot,
        filename=upload.filename,
        url=f"/files/upload/{upload.id}",
        md5=digest,
        size=len(content),
    )


@router.get("", response_model=list[UploadListItemOut])
def list_uploads(
    limit: int = Query(default=200, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前用户的上传素材列表（资产页数据源，按时间倒序）。"""
    rows = (
        db.query(Upload)
        .filter(Upload.user_id == user.id)
        .order_by(Upload.id.desc())
        .limit(limit)
        .all()
    )
    _backfill_fingerprints(rows, db)
    dup_count: dict[str, int] = {}
    for u in rows:
        if u.md5:
            dup_count[u.md5] = dup_count.get(u.md5, 0) + 1
    return [
        UploadListItemOut(
            id=u.id,
            slot=u.slot,
            filename=u.filename,
            url=f"/files/upload/{u.id}",
            kind=_kind_by_path(u.path),
            created_at=u.created_at,
            md5=u.md5 or "",
            size=u.size or 0,
            dup_count=dup_count.get(u.md5, 1) if u.md5 else 1,
        )
        for u in rows
    ]


@router.get("/duplicates", response_model=DedupPreviewOut)
def list_duplicates(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重复素材分组预览（同内容保留最新一条；被历史任务引用的记录列为保护项）。"""
    rows = db.query(Upload).filter(Upload.user_id == user.id).order_by(Upload.id.desc()).all()
    _backfill_fingerprints(rows, db)
    referenced = _referenced_upload_ids(db, user.id)
    groups: list[DedupGroupOut] = []
    protected_total = 0
    for digest, items in _dup_groups(rows).items():
        keep = items[0]
        removable = [u for u in items[1:] if u.id not in referenced]
        protected = len(items) - 1 - len(removable)
        protected_total += protected
        if not removable:
            continue
        groups.append(
            DedupGroupOut(
                md5=digest,
                kind=_kind_by_path(keep.path),
                filename=keep.filename,
                url=f"/files/upload/{keep.id}",
                created_at=keep.created_at,
                size=keep.size or 0,
                count=len(items),
                keep_id=keep.id,
                remove_ids=[u.id for u in removable],
                removable_bytes=sum(u.size or 0 for u in removable),
                protected_count=protected,
            )
        )
    groups.sort(key=lambda g: g.removable_bytes, reverse=True)
    return DedupPreviewOut(
        groups=groups,
        group_count=len(groups),
        removable_count=sum(len(g.remove_ids) for g in groups),
        removable_bytes=sum(g.removable_bytes for g in groups),
        protected_count=protected_total,
    )


@router.post("/dedup", response_model=DedupResultOut)
def dedup_uploads(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """一键去重：同内容保留最新一条，删除其余（连带磁盘文件）；被任务引用的不删。"""
    preview = list_duplicates(user=user, db=db)
    removed = 0
    freed = 0
    for group in preview.groups:
        for upload_id in group.remove_ids:
            row = db.get(Upload, upload_id)
            if row is None or row.user_id != user.id:
                continue
            try:
                Path(row.path).unlink(missing_ok=True)
            except OSError:
                pass  # 文件已不存在时仍清理记录
            removed += 1
            freed += row.size or 0
            db.delete(row)
    db.commit()
    return DedupResultOut(
        removed=removed,
        freed_bytes=freed,
        groups=preview.group_count,
        protected_count=preview.protected_count,
    )


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(
    upload_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除自己的上传素材（连带删除磁盘文件）。"""
    upload = db.get(Upload, upload_id)
    if upload is None or upload.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "素材不存在")
    try:
        Path(upload.path).unlink(missing_ok=True)
    except OSError:
        # 磁盘文件已不存在时仍允许清理记录
        pass
    db.delete(upload)
    db.commit()

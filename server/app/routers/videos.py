"""视频任务路由：提交 / 列表 / 详情 / 重试 / 删除 / 价格。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..config import settings
from ..database import get_db
from ..models import Task, User
from ..schemas import PackageOut, PricingOut, TaskOut, VideoCreateIn
from ..services.billing import PACKAGES, compute_cost
from .serialize import serialize_task

router = APIRouter(prefix="/api/videos", tags=["videos"])


@router.get("/pricing", response_model=PricingOut)
def pricing():
    return PricingOut(
        signup_bonus=settings.signup_bonus,
        cost_768p_5s=settings.cost_768p_5s,
        cost_768p_10s=settings.cost_768p_10s,
        cost_1k_extra=settings.cost_1k_extra,
        cost_2k_extra=settings.cost_2k_extra,
        cloud_enabled=settings.cloud_enabled,
        upscale_enabled=settings.upscale_enabled,
        packages=[PackageOut(**p) for p in PACKAGES],
    )


@router.post("", response_model=TaskOut)
def create_video(
    body: VideoCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 模式与输入图校验
    if body.mode == "flf2v" and not (body.first_image_id or body.last_image_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "首尾帧模式需要至少上传首帧或尾帧图片")
    if body.mode == "r2v" and not body.ref_image_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "全能参考模式需要上传参考图")
    # 1K/2K 升级依赖本地超分池（关闭时 2K 需云端降级通道）
    if body.resolution in ("1k", "2k"):
        if not settings.upscale_enabled and not (body.resolution == "2k" and settings.cloud_enabled):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "本地超分池未启用，暂不支持该分辨率档位")

    cost = compute_cost(body.duration, body.resolution)
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"积分不足，本次生成需 {cost} 积分，请先充值")

    task = Task(
        user_id=user.id,
        mode=body.mode,
        prompt=body.prompt.strip(),
        aspect_ratio=body.aspect_ratio,
        duration=body.duration,
        resolution=body.resolution,
        enhance=body.enhance,
        first_image_id=body.first_image_id if body.mode == "flf2v" else None,
        last_image_id=body.last_image_id if body.mode == "flf2v" else None,
        ref_image_ids=json.dumps(body.ref_image_ids) if body.mode == "r2v" else "",
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return serialize_task(task)


@router.get("", response_model=list[TaskOut])
def list_videos(
    limit: int = Query(default=50, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tasks = (
        db.query(Task)
        .filter(Task.user_id == user.id)
        .order_by(Task.id.desc())
        .limit(limit)
        .all()
    )
    return [serialize_task(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskOut)
def get_video(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    return serialize_task(task)


@router.post("/{task_id}/retry", response_model=TaskOut)
def retry_video(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    if task.status != "failed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅失败任务可重试")
    cost = compute_cost(task.duration, task.resolution)
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"积分不足，重试需 {cost} 积分")
    task.status = "queued"
    task.error = ""
    task.cost = 0
    task.video_path = ""
    task.attempts = 0
    task.worker_url = ""
    task.started_at = None
    task.finished_at = None
    db.commit()
    db.refresh(task)
    return serialize_task(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    if task.status in ("enhancing", "generating_768p", "upscaling"):
        raise HTTPException(status.HTTP_409_CONFLICT, "任务执行中，暂不可删除")
    db.delete(task)
    db.commit()


def _get_owned_task(task_id: int, user: User, db: Session) -> Task:
    task = db.get(Task, task_id)
    if task is None or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return task

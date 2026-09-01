"""任务序列化：ORM → TaskOut（附带带鉴权的文件地址）。"""
import json
from typing import Optional

from ..models import Task
from ..schemas import TaskOut


def serialize_task(task: Task, user_email: Optional[str] = None) -> TaskOut:
    try:
        ref_ids = json.loads(task.ref_image_ids or "[]")
    except (TypeError, ValueError):
        ref_ids = []
    return TaskOut(
        id=task.id,
        mode=task.mode,
        prompt=task.prompt,
        enhanced_prompt=task.enhanced_prompt or "",
        aspect_ratio=task.aspect_ratio,
        duration=task.duration,
        resolution=task.resolution,
        enhance=task.enhance,
        status=task.status,
        error=task.error or "",
        cost=task.cost,
        video_url=f"/files/video/{task.id}" if task.status == "done" else None,
        first_image_url=f"/files/upload/{task.first_image_id}" if task.first_image_id else None,
        last_image_url=f"/files/upload/{task.last_image_id}" if task.last_image_id else None,
        ref_image_urls=[f"/files/upload/{i}" for i in ref_ids],
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        user_email=user_email,
    )

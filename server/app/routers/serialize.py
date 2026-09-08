"""任务序列化：ORM → TaskOut（附带带鉴权的文件地址）。"""
import json
from typing import Optional

from ..config import OUTPUT_DIR
from ..models import Task
from ..schemas import TaskOut


def serialize_task(task: Task, user_email: Optional[str] = None) -> TaskOut:
    try:
        ref_ids = json.loads(task.ref_image_ids or "[]")
    except (TypeError, ValueError):
        ref_ids = []

    # 视频地址：优先最高可用分辨率
    video_url: Optional[str] = None
    upscale_urls: dict[str, str] = {}

    if task.status == "done":
        # 检查各分辨率成片是否存在
        for res, suffix in (("2k", "_2k"), ("1k", "_1k")):
            path = OUTPUT_DIR / f"{task.id}{suffix}.mp4"
            if path.exists():
                upscale_urls[res] = f"/files/video/{task.id}?resolution={res}"

        # video_url 指向最高可用分辨率
        if (OUTPUT_DIR / f"{task.id}_2k.mp4").exists():
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
        status=task.status,
        error=task.error or "",
        cost=task.cost,
        video_url=video_url,
        upscale_urls=upscale_urls,
        first_image_url=f"/files/upload/{task.first_image_id}" if task.first_image_id else None,
        last_image_url=f"/files/upload/{task.last_image_id}" if task.last_image_id else None,
        ref_image_urls=[f"/files/upload/{i}" for i in ref_ids],
        parent_task_id=task.parent_task_id,
        upscale_target=task.upscale_target,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        user_email=user_email,
    )

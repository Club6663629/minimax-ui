"""任务 Worker：单并发串行消费任务队列（GPU 并发=1），驱动三段式流水线。

状态机：queued → enhancing(可选) → generating_768p → upscaling_2k(可选) → done / failed
计费：进入 generating_768p 时扣积分，失败全额退还（见部署方案 8.2）。
Mock 模式（MOCK_COMFY=1）：不依赖 ComfyUI，用 ffmpeg 生成测试视频，用于前端联调。
"""
import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import OUTPUT_DIR, STAGING_DIR, settings
from ..database import SessionLocal
from ..models import Task, Upload, User
from . import cloud, comfyui
from .billing import add_credits, compute_cost

logger = logging.getLogger(__name__)

_worker_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------- 生命周期
def start_worker() -> None:
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.get_event_loop().create_task(_worker_loop())
        logger.info("任务 Worker 已启动 (mock=%s)", settings.mock_comfy)


async def stop_worker() -> None:
    global _worker_task
    if _worker_task is not None:
        _worker_task.cancel()
        _worker_task = None


async def _worker_loop() -> None:
    while True:
        try:
            task_id = await asyncio.to_thread(_claim_next)
            if task_id is None:
                await asyncio.sleep(2)
                continue
            logger.info("开始处理任务 #%s", task_id)
            await _process(task_id)
            logger.info("任务 #%s 处理结束", task_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # Worker 永不退出
            logger.exception("Worker 主循环异常")
            await asyncio.sleep(3)


# ---------------------------------------------------------------- 队列
def _claim_next() -> Optional[int]:
    """领取最早入队的一个任务，进入首个执行阶段。"""
    db: Session = SessionLocal()
    try:
        task = db.query(Task).filter(Task.status == "queued").order_by(Task.id).first()
        if task is None:
            return None
        if task.enhance and settings.cloud_enabled:
            task.status = "enhancing"
        else:
            task.status = "generating_768p"
        db.commit()
        return task.id
    finally:
        db.close()


# ---------------------------------------------------------------- 主流程
async def _process(task_id: int) -> None:
    try:
        # ① 提示词增强（失败不阻塞，退回原始提示词）
        with SessionLocal() as db:
            task: Task = db.get(Task, task_id)
            if task.status == "enhancing":
                try:
                    enhanced = await cloud.enhance_prompt(
                        task.prompt, task.duration, task.aspect_ratio
                    )
                    task.enhanced_prompt = enhanced
                    logger.info("任务 #%s 提示词增强完成", task_id)
                except Exception as exc:
                    logger.warning("任务 #%s 增强失败，使用原始提示词: %s", task_id, exc)
                task.status = "generating_768p"
                db.commit()

        # 扣积分（进入 generating_768p 时）
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            user: User = db.get(User, task.user_id)
            cost = compute_cost(task.duration, task.resolution)
            if user.credits < cost:
                task.status = "failed"
                task.error = "积分不足"
                task.finished_at = datetime.utcnow()
                db.commit()
                return
            add_credits(
                db, user, -cost, "consume",
                note=f"视频生成 {task.duration}s/{task.resolution}", task_id=task_id,
            )
            task.cost = cost
            task.started_at = datetime.utcnow()
            db.commit()

        # ② 本地 768p 生成
        staging_768p = await _generate_768p(task_id)

        # ③ 2K 重生成（可选）
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            need_2k = task.resolution == "2k"

        final_path: Path
        if need_2k:
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                task.status = "upscaling_2k"
                db.commit()
            path_2k = await cloud.regenerate_2k(
                staging_768p, task.aspect_ratio, task.duration
            )
            final_path = OUTPUT_DIR / f"{task_id}_2k.mp4"
            shutil.move(str(path_2k), final_path)
        else:
            final_path = OUTPUT_DIR / f"{task_id}.mp4"
            shutil.move(str(staging_768p), final_path)

        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.video_path = str(final_path)
            task.status = "done"
            task.finished_at = datetime.utcnow()
            db.commit()
        logger.info("任务 #%s 完成: %s", task_id, final_path)

    except Exception as exc:
        logger.exception("任务 #%s 失败", task_id)
        await asyncio.to_thread(_mark_failed, task_id, str(exc)[:500])


def _mark_failed(task_id: int, error: str) -> None:
    db: Session = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.status in ("done", "failed"):
            return
        task.status = "failed"
        task.error = error
        task.finished_at = datetime.utcnow()
        if task.cost > 0:  # 失败退还积分
            user = db.get(User, task.user_id)
            add_credits(db, user, task.cost, "refund", note="生成失败退还", task_id=task_id)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- ② 768p 生成
async def _generate_768p(task_id: int) -> Path:
    """生成 768p 视频，返回 staging 目录中的中间产物路径。"""
    out_path = STAGING_DIR / f"{task_id}_768p.mp4"

    if settings.mock_comfy:
        await asyncio.sleep(6)  # 模拟排队+生成耗时
        _make_mock_video(out_path)
        return out_path

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        final_prompt = task.enhanced_prompt or task.prompt
        mode, ratio, duration = task.mode, task.aspect_ratio, task.duration
        image_paths: list[Path] = []
        try:
            ref_ids = json.loads(task.ref_image_ids or "[]")
        except (TypeError, ValueError):
            ref_ids = []
        for img_id in (task.first_image_id, task.last_image_id, *ref_ids):
            if img_id:
                up = db.get(Upload, img_id)
                if up:
                    image_paths.append(Path(up.path))

    client = comfyui.ComfyUIClient()

    # 输入图先传到 ComfyUI
    image_names = [await client.upload_image(p) for p in image_paths]

    workflow = comfyui.load_workflow(mode)
    comfyui.inject(workflow, mode, final_prompt, ratio, duration, image_names)

    prompt_id = await client.submit(workflow)
    with SessionLocal() as db:
        db.get(Task, task_id).comfy_prompt_id = prompt_id
        db.commit()

    file_info = await client.wait_result(prompt_id)
    content = await client.fetch_file(
        file_info["filename"], file_info.get("subfolder", ""), file_info.get("type", "output")
    )
    out_path.write_bytes(content)
    logger.info("任务 #%s 768p 中间产物已落盘: %s", task_id, out_path)
    return out_path


def _make_mock_video(out_path: Path) -> None:
    """Mock 模式：优先用 ffmpeg 生成带音轨的测试视频，否则写占位文件。"""
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=5",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(out_path),
            ],
            check=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        logger.warning("ffmpeg 不可用，写入占位视频文件")
        out_path.write_bytes(b"MOCK-VIDEO-PLACEHOLDER")

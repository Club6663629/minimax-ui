"""任务调度：双队列并发流水线（见《H3集群部署方案》§6 编排器路由策略）。

状态机：queued → enhancing(可选) → generating_768p → upscaling(1k/2k，可选) → done / failed
计费：派发本地生成时扣积分，失败全额退还（见部署方案 8.2）。

三个常驻循环（单进程内，领取均用条件 UPDATE 原子领取，天然无并发竞争）：
- _enhance_loop   领取 queued → 云端提示词增强（不占 GPU 槽位）
- _generate_loop  派发生成槽位（时长>10s 优先 heavy 节点）；队列深度超阈值且
                  配置了云端 Key 时，最老的排队任务转云端全流程降级
- _upscale_loop   派发超分槽位（2K 优先；主力池优先，1K 溢出节点兜底）

失败自愈：执行中节点失联/报错且 attempts < 2 → 状态回退重投；超限失败退积分；
2K 超分重试耗尽且配置了云端 Key → 回落云端重生成（降级通道）。
Mock 模式（MOCK_COMFY=1）：不依赖 ComfyUI，用 ffmpeg 生成测试视频，模拟多节点并发。
"""
import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import OUTPUT_DIR, STAGING_DIR, settings
from ..database import SessionLocal
from ..models import Task, Upload, User
from . import cloud, comfyui
from .billing import add_credits, compute_cost
from .pool import WorkerNode, pool

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2          # 每阶段最多执行次数（含首次）
ENHANCE_CONCURRENCY = 4   # 提示词增强并发上限（云端接口调用，不占 GPU）

_bg_tasks: list = []


# ---------------------------------------------------------------- 生命周期
def start_worker() -> None:
    if _bg_tasks:
        return
    pool.reset_all()
    loop = asyncio.get_event_loop()
    _bg_tasks.append(loop.create_task(pool.start()))
    _bg_tasks.append(loop.create_task(_loop_guard(_enhance_loop, "增强")))
    _bg_tasks.append(loop.create_task(_loop_guard(_generate_loop, "生成")))
    _bg_tasks.append(loop.create_task(_loop_guard(_upscale_loop, "超分")))
    _bg_tasks.append(loop.create_task(_recover_orphans()))
    logger.info("调度器已启动 (mock=%s)", settings.mock_comfy)


async def stop_worker() -> None:
    for t in _bg_tasks:
        t.cancel()
    _bg_tasks.clear()
    await pool.stop()


async def _loop_guard(coro_fn, name: str) -> None:
    """常驻循环守护：任何异常都不退出。"""
    while True:
        try:
            await coro_fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s调度循环异常", name)
            await asyncio.sleep(3)


# ---------------------------------------------------------------- 原子领取
def _claim(task_id: int, from_status: str, to_status: str) -> bool:
    """条件 UPDATE 领取任务；状态已变化则返回 False。"""
    with SessionLocal() as db:
        row = db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == from_status)
            .values(status=to_status)
        ).rowcount
        db.commit()
    return bool(row)


# ---------------------------------------------------------------- ① 提示词增强
async def _enhance_loop() -> None:
    while True:
        running = await asyncio.to_thread(_count_enhancing)
        if running < ENHANCE_CONCURRENCY:
            task_id = await asyncio.to_thread(_claim_queued)
            if task_id is not None:
                asyncio.get_event_loop().create_task(_run_enhance(task_id))
        await asyncio.sleep(2)


def _count_enhancing() -> int:
    with SessionLocal() as db:
        return db.query(Task).filter(Task.status == "enhancing").count()


def _claim_queued() -> Optional[int]:
    """领取最早一个需云端增强的排队任务（不需要增强的由生成循环直接领取）。"""
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(Task.status == "queued", Task.enhance.is_(True))
            .order_by(Task.id)
            .first()
        )
        if task is None or not settings.cloud_enabled:
            return None
        task.status = "enhancing"
        db.commit()
        return task.id


async def _run_enhance(task_id: int) -> None:
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            prompt, duration, ratio = task.prompt, task.duration, task.aspect_ratio
        try:
            enhanced = await cloud.enhance_prompt(prompt, duration, ratio)
            with SessionLocal() as db:
                db.get(Task, task_id).enhanced_prompt = enhanced
            logger.info("任务 #%s 提示词增强完成", task_id)
        except Exception as exc:
            logger.warning("任务 #%s 增强失败，使用原始提示词: %s", task_id, exc)
        # 增强完成（无论成败）进入生成队列
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task.status == "enhancing":
                task.status = "generating_768p"
                db.commit()
    except Exception:
        logger.exception("任务 #%s 增强阶段异常", task_id)
        await asyncio.to_thread(_requeue_or_fail, task_id, "enhancing")


# ---------------------------------------------------------------- 云端降级
async def _maybe_degrade() -> None:
    """生成队列深度（排队+生成阶段）> 阈值且有云端 Key → 最老排队任务转云端全流程。"""
    if not settings.cloud_enabled:
        return
    with SessionLocal() as db:
        depth = (
            db.query(Task)
            .filter(Task.status.in_(("queued", "enhancing", "generating_768p")))
            .count()
        )
    if depth <= settings.degrade_queue_depth:
        return
    # 优先把 2K 任务送上云端（价值档）
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(Task.status == "queued")
            .order_by(Task.resolution.desc(), Task.id)
            .first()
        )
        if task is None:
            return
        task_id = task.id
    if _claim(task_id, "queued", "generating_768p"):
        logger.warning("生成队列深度 %s 超阈值，任务 #%s 转云端全流程", depth, task_id)
        asyncio.get_event_loop().create_task(_run_cloud_full(task_id))


# ---------------------------------------------------------------- ② 生成队列
async def _generate_loop() -> None:
    while True:
        await _maybe_degrade()
        # 无空闲生成槽位时不领取，避免反复扣费/退费的流水噪声
        if not any(n.role == "generate" and n.healthy and not n.busy for n in pool.nodes):
            await asyncio.sleep(2)
            continue
        task_id = await asyncio.to_thread(_claim_generate)
        if task_id is None:
            await asyncio.sleep(2)
            continue
        await _dispatch_generate(task_id)  # 同步派发：占位已在领取时写入，无重复领取窗口


def _claim_generate() -> Optional[int]:
    """领取最早的生成任务（含无需增强、仍在 queued 的）；先扣积分（进入生成即计费）。"""
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(Task.status.in_(("queued", "generating_768p")), Task.worker_url == "")
            .order_by(Task.id)
            .first()
        )
        if task is None:
            return None
        task_id, user_id = task.id, task.user_id
        if task.cost > 0:  # 重投任务已在首次派发时计费，不重复扣款；占位同步写入防再领
            task.status = "generating_768p"
            task.worker_url = "pending"
            db.commit()
            return task_id
        user = db.get(User, user_id)
        cost = compute_cost(task.duration, task.resolution)
        if user.credits < cost:
            task.status = "failed"
            task.error = "积分不足"
            task.finished_at = datetime.now()
            db.commit()
            return None
        add_credits(
            db, user, -cost, "consume",
            note=f"视频生成 {task.duration}s/{task.resolution}", task_id=task_id,
        )
        task.status = "generating_768p"
        task.cost = cost
        task.started_at = datetime.now()
        task.worker_url = "pending"  # 占位与计费同事务提交，防领取后派发前被重复领取
        db.commit()
        return task_id


async def _dispatch_generate(task_id: int) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        heavy = task.duration >= 10
    node = pool.acquire_generate(heavy, task_id)
    if node is None:
        await asyncio.to_thread(_uncharge, task_id)  # 无空闲槽位：退还积分回队
        return
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        task.attempts += 1
        task.worker_url = node.url
        db.commit()
    asyncio.get_event_loop().create_task(_run_generate(task_id, node))


def _uncharge(task_id: int) -> None:
    """派发时暂无槽位：退还已扣积分，任务回到生成队列头部等待。"""
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status != "generating_768p" or task.cost <= 0:
            return
        user = db.get(User, task.user_id)
        add_credits(db, user, task.cost, "refund", note="排队中暂退", task_id=task_id)
        task.cost = 0
        task.started_at = None
        task.worker_url = ""  # 释放占位，回到可领取状态
        db.commit()


async def _run_generate(task_id: int, node: WorkerNode) -> None:
    try:
        staging_768p = await _generate_768p(task_id, node)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task.status != "generating_768p":
                return  # 已被删除等
            need_upscale = settings.upscale_enabled and task.resolution in ("1k", "2k")
            if need_upscale:
                task.status = "upscaling"
                task.worker_url = ""
                db.commit()
                logger.info("任务 #%s 768p 完成，进入超分队列(%s)", task_id, task.resolution)
            else:
                final_path = OUTPUT_DIR / f"{task_id}.mp4"
                shutil.move(str(staging_768p), final_path)
                task.video_path = str(final_path)
                task.status = "done"
                task.finished_at = datetime.now()
                db.commit()
                logger.info("任务 #%s 完成: %s", task_id, final_path)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("任务 #%s 生成失败", task_id)
        await asyncio.to_thread(_requeue_or_fail, task_id, "generating_768p", str(exc)[:500])
    finally:
        pool.release(node)


# ---------------------------------------------------------------- 孤儿任务恢复
async def _recover_orphans() -> None:
    """启动时恢复后端重启前派发、但新进程未认领的中间态任务。

    后端重启会清空内存态（worker_url 非空的任务被各 claim 永久跳过），
    但这些任务在 ComfyUI 节点上可能仍在跑或已跑完。启动时扫描
    generating_768p / upscaling 且 worker_url != "" 的任务，按 comfy_prompt_id
    去对应节点查 /history，已完成则下载落盘推进状态机，仍在跑则重新挂起轮询，
    节点已无该任务则回退重投。

    扫描不要求 comfy_prompt_id 非空：派发后（worker_url 已写）、提交工作流前
    崩溃的任务 prompt_id 为空，同样会被卡住，需一并捞起；恢复函数内部对空
    prompt_id 直接回退重投。
    """
    await asyncio.sleep(3)  # 等 pool 心跳先探活一轮
    with SessionLocal() as db:
        orphans = (
            db.query(Task)
            .filter(
                Task.status.in_(("generating_768p", "upscaling")),
                Task.worker_url != "",
            )
            .all()
        )
    if not orphans:
        return
    logger.info("启动恢复：发现 %d 个中间态任务待恢复", len(orphans))
    for task in orphans:
        node = pool.get_node_by_url(task.worker_url)
        if node is None:
            # 节点已不在池中：回退重投，让调度器重新派发
            logger.warning("孤儿任务 #%s 节点 %s 已不在池中，回退重投", task.id, task.worker_url)
            await asyncio.to_thread(_requeue_or_fail, task.id, task.status)
            continue
        if task.status == "upscaling":
            asyncio.get_event_loop().create_task(_resume_upscale(task.id, node))
        else:
            asyncio.get_event_loop().create_task(_resume_generate(task.id, node))


async def _resume_generate(task_id: int, node: WorkerNode) -> None:
    """恢复一个已派发到节点的生成任务：轮询其 comfy_prompt_id 结果并推进状态机。

    与 _run_generate 的区别：不重新提交工作流，只轮询已有 prompt_id 的结果。
    适用于后端重启后，节点上任务仍在跑或已跑完但未落盘的场景。
    prompt_id 为空（派发后、提交前崩溃）时直接回退重投。
    """
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None or task.status != "generating_768p":
                return
            prompt_id = task.comfy_prompt_id
        if not prompt_id:
            await asyncio.to_thread(_requeue_or_fail, task_id, "generating_768p")
            return
        client = comfyui.ComfyUIClient(node.url)
        file_info = await client.wait_result(prompt_id)
        content = await client.fetch_file(
            file_info["filename"], file_info.get("subfolder", ""), file_info.get("type", "output")
        )
        staging_768p = STAGING_DIR / f"{task_id}_768p.mp4"
        staging_768p.write_bytes(content)
        logger.info("孤儿任务 #%s 768p 产物已恢复落盘 (节点 %s)", task_id, node.url)

        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task.status != "generating_768p":
                return
            need_upscale = settings.upscale_enabled and task.resolution in ("1k", "2k")
            if need_upscale:
                task.status = "upscaling"
                task.worker_url = ""
                db.commit()
                logger.info("孤儿任务 #%s 768p 恢复完成，进入超分队列(%s)", task_id, task.resolution)
            else:
                final_path = OUTPUT_DIR / f"{task_id}.mp4"
                shutil.move(str(staging_768p), final_path)
                task.video_path = str(final_path)
                task.status = "done"
                task.finished_at = datetime.now()
                db.commit()
                logger.info("孤儿任务 #%s 恢复完成: %s", task_id, final_path)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("孤儿任务 #%s 恢复失败", task_id)
        await asyncio.to_thread(_requeue_or_fail, task_id, "generating_768p", str(exc)[:500])
    finally:
        pool.release(node)


async def _resume_upscale(task_id: int, node: WorkerNode) -> None:
    """恢复一个已派发到节点的超分任务：轮询其 comfy_prompt_id 结果并落盘成片。

    与 _run_upscale 的区别：不重新提交工作流，只轮询已有 prompt_id 的结果。
    适用于后端重启后，节点上超分任务仍在跑或已跑完但未落盘的场景。
    prompt_id 为空（派发后、提交前崩溃）时直接回退重投。
    """
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None or task.status != "upscaling":
                return
            prompt_id = task.comfy_prompt_id
        if not prompt_id:
            await asyncio.to_thread(_requeue_or_fail, task_id, "upscaling")
            return
        client = comfyui.ComfyUIClient(node.url)
        file_info = await client.wait_result(prompt_id)
        content = await client.fetch_file(
            file_info["filename"], file_info.get("subfolder", ""), file_info.get("type", "output")
        )
        out_path = STAGING_DIR / f"{task_id}_upscaled.mp4"
        out_path.write_bytes(content)
        src = STAGING_DIR / f"{task_id}_768p.mp4"
        await asyncio.to_thread(_ensure_audio, src, out_path)
        logger.info("孤儿任务 #%s 超分产物已恢复落盘 (节点 %s)", task_id, node.url)

        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task.status != "upscaling":
                return
            suffix = "2k" if task.resolution == "2k" else "1k"
            final_path = OUTPUT_DIR / f"{task_id}_{suffix}.mp4"
        shutil.move(str(out_path), final_path)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.video_path = str(final_path)
            task.status = "done"
            task.finished_at = datetime.now()
            db.commit()
        logger.info("孤儿任务 #%s 超分恢复完成: %s", task_id, final_path)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("孤儿任务 #%s 超分恢复失败", task_id)
        await asyncio.to_thread(_requeue_or_fail, task_id, "upscaling", str(exc)[:500])
    finally:
        pool.release(node)


# ---------------------------------------------------------------- ③ 超分队列
async def _upscale_loop() -> None:
    while True:
        task_id = await asyncio.to_thread(_claim_upscale)
        if task_id is None:
            await asyncio.sleep(2)
            continue
        await _dispatch_upscale(task_id)  # 同步派发，避免重复领取窗口


def _claim_upscale() -> Optional[int]:
    """领取超分任务：2K 优先派发（价值档）；1K 短，用于填补主力空窗。
    本地超分池关闭时：2K 有云端则转云端重生成（负数 ID 标记），否则完成/失败。"""
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(Task.status == "upscaling", Task.worker_url == "")
            .order_by(Task.resolution.desc(), Task.id)
            .first()
        )
        if task is None:
            return None
        task_id, tier = task.id, task.resolution
        no_local_pool = not settings.upscale_enabled or not pool.has_upscale()
        if no_local_pool and not pool.mock:
            if tier == "2k" and settings.cloud_enabled:
                task.worker_url = "cloud"  # 占位，由云端分支处理（防重复领取）
                db.commit()
                return -task_id  # 负数表示云端超分
            task.status = "failed"
            task.error = "超分池不可用（未配置超分节点）"
            task.finished_at = datetime.now()
            if task.cost > 0:
                add_credits(db, db.get(User, task.user_id), task.cost, "refund",
                            note="超分不可用退还", task_id=task.id)
            db.commit()
            return None
        return task_id


async def _dispatch_upscale(task_id: int) -> None:
    if task_id < 0:  # 本地超分关闭 → 云端 2K 重生成
        asyncio.get_event_loop().create_task(_run_cloud_upscale(-task_id))
        return
    with SessionLocal() as db:
        tier = db.get(Task, task_id).resolution
    node = pool.acquire_upscale(tier, task_id)
    if node is None:
        return
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        task.attempts += 1
        task.worker_url = node.url
        db.commit()
    asyncio.get_event_loop().create_task(_run_upscale(task_id, node))


async def _run_upscale(task_id: int, node: WorkerNode) -> None:
    try:
        path_hd = await _upscale(task_id, node)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task.status != "upscaling":
                return
            suffix = "2k" if task.resolution == "2k" else "1k"
            final_path = OUTPUT_DIR / f"{task_id}_{suffix}.mp4"
        shutil.move(str(path_hd), final_path)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.video_path = str(final_path)
            task.status = "done"
            task.finished_at = datetime.now()
            db.commit()
        logger.info("任务 #%s 超分完成: %s", task_id, final_path)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("任务 #%s 超分失败", task_id)
        if await asyncio.to_thread(_requeue_or_fail, task_id, "upscaling", str(exc)[:500]):
            pass  # 已重投超分队列
        else:
            # 重试耗尽：2K 且有云端 → 回落云端重生成（降级通道）
            with SessionLocal() as db:
                task = db.get(Task, task_id)
                fallback = (
                    task is not None and task.status == "failed"
                    and task.resolution == "2k" and settings.cloud_enabled
                )
            if fallback:
                logger.warning("任务 #%s 本地超分重试耗尽，回落云端 2K", task_id)
                asyncio.get_event_loop().create_task(_run_cloud_upscale(task_id))
    finally:
        pool.release(node)


async def _run_cloud_upscale(task_id: int) -> None:
    """云端 2K 重生成（本地超分关闭或重试耗尽时的降级通道）。"""
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            staging_768p = STAGING_DIR / f"{task_id}_768p.mp4"
            ratio, duration = task.aspect_ratio, task.duration
        path_2k = await cloud.regenerate_2k(staging_768p, ratio, duration)
        final_path = OUTPUT_DIR / f"{task_id}_2k.mp4"
        shutil.move(str(path_2k), final_path)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.video_path = str(final_path)
            task.status = "done"
            task.worker_url = "cloud"
            task.finished_at = datetime.now()
            db.commit()
        logger.info("任务 #%s 云端 2K 完成: %s", task_id, final_path)
    except Exception as exc:
        logger.exception("任务 #%s 云端 2K 失败", task_id)
        await asyncio.to_thread(_mark_failed, task_id, f"云端2K失败: {str(exc)[:300]}")


async def _run_cloud_full(task_id: int) -> None:
    """云端全流程降级：增强(可选) + 官方 API 直接出成片。"""
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            need_enhance = task.enhance
            prompt, duration, ratio = task.prompt, task.duration, task.aspect_ratio
        if need_enhance:
            try:
                prompt = await cloud.enhance_prompt(prompt, duration, ratio)
                with SessionLocal() as db:
                    db.get(Task, task_id).enhanced_prompt = prompt
            except Exception as exc:
                logger.warning("任务 #%s 云端增强失败，用原始提示词: %s", task_id, exc)
        first_path = last_path = None
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            for slot, attr in ((task.first_image_id, "first"), (task.last_image_id, "last")):
                if slot:
                    up = db.get(Upload, slot)
                    if up:
                        if attr == "first":
                            first_path = Path(up.path)
                        else:
                            last_path = Path(up.path)
        cloud_video = await cloud.generate_video(prompt, duration, ratio, first_path, last_path)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            suffix = f"_{task.resolution}" if task.resolution != "768p" else ""
            final_path = OUTPUT_DIR / f"{task_id}{suffix}.mp4"
        shutil.move(str(cloud_video), final_path)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            task.video_path = str(final_path)
            task.status = "done"
            task.worker_url = "cloud"
            task.finished_at = datetime.now()
            db.commit()
        logger.info("任务 #%s 云端降级完成: %s", task_id, final_path)
    except Exception as exc:
        logger.exception("任务 #%s 云端降级失败", task_id)
        await asyncio.to_thread(_mark_failed, task_id, str(exc)[:500])


# ---------------------------------------------------------------- 失败处理
def _requeue_or_fail(task_id: int, stage: str, error: str = "") -> bool:
    """attempts < MAX 回退状态重投；否则失败退积分。返回是否已重投。"""
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status in ("done", "failed"):
            return False
        if task.attempts < MAX_ATTEMPTS:
            task.status = stage
            task.worker_url = ""
            db.commit()
            logger.warning("任务 #%s 回退重投（%s，已尝试 %d 次）", task_id, stage, task.attempts)
            return True
        task.status = "failed"
        task.error = error or "执行失败"
        task.finished_at = datetime.now()
        if task.cost > 0:  # 失败退还积分
            user = db.get(User, task.user_id)
            add_credits(db, user, task.cost, "refund", note="生成失败退还", task_id=task_id)
        db.commit()
    return False


def _mark_failed(task_id: int, error: str) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status in ("done", "failed"):
            return
        task.status = "failed"
        task.error = error
        task.finished_at = datetime.now()
        if task.cost > 0:  # 失败退还积分
            user = db.get(User, task.user_id)
            add_credits(db, user, task.cost, "refund", note="生成失败退还", task_id=task_id)
        db.commit()


# ---------------------------------------------------------------- ② 768p 生成
async def _generate_768p(task_id: int, node: WorkerNode) -> Path:
    """在指定节点生成 768p 视频，返回 staging 目录中的中间产物路径。"""
    out_path = STAGING_DIR / f"{task_id}_768p.mp4"

    if settings.mock_comfy:
        await asyncio.sleep(6)  # 模拟排队+生成耗时
        _make_mock_video(out_path)
        return out_path

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        final_prompt = task.enhanced_prompt or task.prompt
        mode, ratio, duration = task.mode, task.aspect_ratio, task.duration
        image_paths: list = []
        try:
            ref_ids = json.loads(task.ref_image_ids or "[]")
        except (TypeError, ValueError):
            ref_ids = []
        for img_id in (task.first_image_id, task.last_image_id, *ref_ids):
            if img_id:
                up = db.get(Upload, img_id)
                if up:
                    image_paths.append(Path(up.path))

    client = comfyui.ComfyUIClient(node.url)

    # 输入图先传到该节点的 input 目录
    image_names = [await client.upload_image(p) for p in image_paths]

    workflow = comfyui.load_workflow(mode)
    comfyui.inject(workflow, mode, final_prompt, ratio, duration, image_names,
                   unet_name=node.unet_for(mode))

    prompt_id = await client.submit(workflow)
    with SessionLocal() as db:
        db.get(Task, task_id).comfy_prompt_id = prompt_id
        db.commit()

    file_info = await client.wait_result(prompt_id)
    content = await client.fetch_file(
        file_info["filename"], file_info.get("subfolder", ""), file_info.get("type", "output")
    )
    out_path.write_bytes(content)
    logger.info("任务 #%s 768p 中间产物已落盘: %s (节点 %s)", task_id, out_path, node.url)
    return out_path


# ---------------------------------------------------------------- ③ 本地超分
async def _upscale(task_id: int, node: WorkerNode) -> Path:
    """在指定超分节点执行 SeedVR2 超分，返回 staging 中的成片路径。"""
    out_path = STAGING_DIR / f"{task_id}_upscaled.mp4"

    if settings.mock_comfy:
        await asyncio.sleep(4)
        _make_mock_video(out_path)
        return out_path

    src = STAGING_DIR / f"{task_id}_768p.mp4"
    client = comfyui.ComfyUIClient(node.url)
    video_name = await client.upload_file(src)

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        tier, ratio = task.resolution, task.aspect_ratio

    # 按节点引擎选模板：engine:seedvr2 → 原生节点；默认 KSampler 管线（模板默认 3B FP16 权重）
    _engine_tpl = {"seedvr2": "upscale_7b", "seedvr2_3090": "upscale_3090"}
    template = _engine_tpl.get(node.engine, "upscale")
    workflow = comfyui.load_workflow(template)
    comfyui.inject_upscale(workflow, video_name, tier, ratio, unet_name=node.unet)

    prompt_id = await client.submit(workflow)
    with SessionLocal() as db:
        db.get(Task, task_id).comfy_prompt_id = prompt_id
        db.commit()

    file_info = await client.wait_result(prompt_id)
    content = await client.fetch_file(
        file_info["filename"], file_info.get("subfolder", ""), file_info.get("type", "output")
    )
    out_path.write_bytes(content)
    await asyncio.to_thread(_ensure_audio, src, out_path)
    logger.info("任务 #%s 超分产物已落盘: %s (节点 %s)", task_id, out_path, node.url)
    return out_path


def _ensure_audio(src: Path, out_path: Path) -> None:
    """超分工作流已带原音轨；若产物无音轨而源片有，用 ffmpeg 补合（防御性）。"""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, timeout=30,
        )
        if probe.stdout.strip():
            return
        merged = out_path.with_suffix(".merged.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_path), "-i", str(src),
             "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-shortest",
             str(merged)],
            check=True, timeout=600,
        )
        shutil.move(str(merged), str(out_path))
    except (FileNotFoundError, subprocess.SubprocessError):
        logger.warning("音轨校验/合并跳过（ffmpeg 不可用或无音轨）")


# ---------------------------------------------------------------- Mock
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

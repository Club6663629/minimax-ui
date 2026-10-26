"""任务调度：双队列并发流水线（见《H3集群部署方案》§6 编排器路由策略）。

状态机：queued → enhancing(可选) → generating_768p → upscaling(1k/2k，可选) → done / failed
计费：派发本地生成时扣积分，失败全额退还（见部署方案 8.2）。

三个常驻循环（单进程内，领取均用条件 UPDATE 原子领取，天然无并发竞争）：
- _enhance_loop   领取 queued → 云端提示词增强（Context-IR，不占 GPU 槽位）
- _generate_loop  派发生成槽位（时长>10s 优先 heavy 节点）
- _upscale_loop   派发超分槽位（2K 优先；主力池优先，1K 溢出节点兜底）

失败自愈：执行中节点失联/报错且 attempts < 2 → 状态回退重投；超限失败退积分。
生成与超分仅走本地 worker；云端仅保留提示词增强，无任何云端生成/超分降级通道。
Mock 模式（MOCK_COMFY=1）：不依赖 ComfyUI，用 ffmpeg 生成测试视频，模拟多节点并发。
"""
import asyncio
import json
import logging
import os
import random
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from ..config import OUTPUT_DIR, STAGING_DIR, settings
from ..database import SessionLocal
from ..models import Task, Upload, User
from . import advconfirm, advenhance, advpostir, advprompt, cloud, comfyui, drama_media, videollm
from ..scenes import apply_scene
from .billing import add_credits, compute_cost, compute_director_cost
from .pool import WorkerNode, pool
from . import quarantine as poison_guard  # POISON_GUARD: 毒丸隔离

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2          # 每阶段最多执行次数（含首次）
ENHANCE_CONCURRENCY = 4   # 提示词增强并发上限（云端接口调用，不占 GPU）


def _video_prompt_mode(task) -> str:
    """生视频提示词增强路由（advideo9）：任务级覆盖 > 全局默认。

    llm        = 主方案（默认）：qwen3.8-flash 视频增强 agent（videollm）；失败回退 content_ir；
    content_ir = C 臂（备选）：Content-IR + 电商 skill（商品保真硬约束）+ advpostir 单镜化后处理；
    local      = A 臂：本地规则增强（advenhance.enhance_video_prompt，0 付费）。
    """
    m = str(getattr(task, "video_prompt_mode", "") or "").strip().lower()
    if m not in ("llm", "content_ir", "local"):
        m = str(getattr(settings, "advideo_video_prompt_mode", "llm") or "llm").strip().lower()
    return m if m in ("llm", "content_ir", "local") else "llm"


def _h3_gen_mode(mode: str, first_id, last_id, ref_ids, is_ad: bool) -> tuple:
    """把任务模式映射为 H3 生成模式标签 + 参考素材提示。

    videollm 据此选择加载 base-en（T2VA/I2VA/FL2VA/L2VA）还是 ref-en（Ref2VA）结构指南，
    并在正文中保持 `<Picture N>` 标签一致。
    """
    n_ref = len(ref_ids or [])
    if is_ad:
        return "Ref2VA", "商品参考图（人工确认的广告图/商品图，均以 <Picture N> 指代，商品本体不得改变）"
    # 与 _generate_768p 的工作流选择口径对齐：只要有参考图就走 r2v（Ref2VA），
    # 从而装载 H3 ref-en 参考/拼贴(montage)规则并用 <Picture N> 指代（人物多姿态/多机位拼贴图也能正确处理）。
    if n_ref or mode == "r2v":
        return "Ref2VA", (f"{n_ref} 份全能参考资料（图/视频/音频，以 <Picture N>/<Video N>/<Audio N> 指代）" if n_ref else "参考图")
    if mode == "flf2v":
        if first_id and last_id:
            return "FL2VA", "首帧图 <Picture 1> + 尾帧图 <Picture 2>"
        if first_id:
            return "I2VA", "首帧图 <Picture 1>"
        if last_id:
            return "L2VA", "尾帧图 <Picture 1>"
    return "T2VA", ""


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
    _bg_tasks.append(loop.create_task(_loop_guard(_advideo_image_loop, "广告图")))
    _bg_tasks.append(loop.create_task(_loop_guard(_studio_images_loop, "剧集设定图")))
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
            .filter(Task.status == "queued", Task.enhance.is_(True), Task.mode != "director")
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
            scene = task.scene
            mode = task.mode
            vmode = _video_prompt_mode(task)   # llm=主方案（默认）/ content_ir=C臂备选 / local=A臂
            first_id, last_id = task.first_image_id, task.last_image_id
            ref_ids = json.loads(task.ref_image_ids or "[]")
            # 收集参考图本地路径，供 videollm 视觉增强（仅图片；口径对齐 _generate_768p 的 H3 参考装配）
            _img_exts = {".jpg", ".jpeg", ".png", ".webp"}
            img_paths: list = []

            def _add_upload(uid) -> None:
                if not uid:
                    return
                up = db.get(Upload, uid)
                if up and Path(up.path).suffix.lower() in _img_exts:
                    img_paths.append(str(up.path))

            if mode == "advideo":
                chosen = str(getattr(task, "chosen_image", "") or "")
                if chosen and Path(chosen).suffix.lower() in _img_exts:
                    img_paths.append(chosen)          # 人工确认的广告图（视频首帧/主参考）
                try:
                    ad_ids = json.loads(task.ad_input_ids or "[]")
                except (TypeError, ValueError):
                    ad_ids = []
                for uid in ad_ids:                    # 商品图 + 参考图（videollm 侧再按上限截断）
                    _add_upload(uid)
            else:
                _add_upload(first_id)
                _add_upload(last_id)
                for rid in ref_ids:
                    _add_upload(rid)
        try:
            is_ad = mode == "advideo"
            # advideo 强制走电商 skill：通用 skill 为空、无商品保真约束，会自创穿搭（advideo8 实测 → 商品一致性 FAIL）
            scene_eff = "ecommerce" if is_ad else scene
            gen_mode, ref_hint = _h3_gen_mode(mode, first_id, last_id, ref_ids, is_ad)
            enhanced = ""
            # ---- 路由决策：主方案（qwen-flash LLM）是否启用 ----
            if is_ad:
                ad_enhance_on = (vmode != "local") and bool(getattr(settings, "advideo_video_prompt_enhance", True))
                use_llm = ad_enhance_on and vmode == "llm" and settings.video_llm_enabled
                ir_allowed = ad_enhance_on   # local(A臂) → 既不 LLM 也不 IR（0 付费，交本地规则层）
                if not ad_enhance_on:
                    logger.info(
                        "任务 #%s advideo：跳过增强（路由=%s，0 付费）",
                        task_id, "local(A臂·本地规则)" if vmode == "local" else "增强总开关关闭",
                    )
            else:
                vem = str(getattr(settings, "video_enhance_mode", "llm") or "llm").strip().lower()
                use_llm = vem == "llm" and settings.video_llm_enabled
                ir_allowed = vem != "off"
                if vem == "off":
                    logger.info("任务 #%s：video_enhance_mode=off，跳过增强", task_id)
            # ---- 主方案：qwen3.8-flash 视频增强 agent ----
            if use_llm:
                try:
                    res = await videollm.enhance_video_prompt(
                        prompt=prompt, scene=scene_eff, gen_mode=gen_mode,
                        ref_hint=ref_hint, duration=duration, ratio=ratio,
                        fidelity=(is_ad or scene_eff == "ecommerce"),
                        image_paths=img_paths,
                    )
                    enhanced = str(res.get("text") or "")
                    logger.info(
                        "任务 #%s LLM 视频增强成功（scene=%s，mode=%s，参考图=%d，%d 字符）",
                        task_id, scene_eff, gen_mode, int(res.get("images") or 0), len(enhanced),
                    )
                except videollm.VideoLLMError as exc:
                    enhanced = ""
                    logger.warning("任务 #%s LLM 视频增强失败，回退 Content-IR: %s", task_id, exc)
            # ---- 备选：云端 Content-IR（LLM 未启用/失败且允许时）----
            if (not enhanced) and ir_allowed:
                if settings.cloud_enabled:
                    compiled = apply_scene(prompt, scene_eff)
                    if is_ad:
                        logger.info("任务 #%s advideo：Content-IR（C臂·电商 skill + 单镜化后处理）", task_id)
                    try:
                        enhanced = await cloud.enhance_prompt(compiled, duration, ratio)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("任务 #%s Content-IR 增强失败，使用原始提示词: %s", task_id, exc)
                        enhanced = ""
                else:
                    logger.info("任务 #%s：Content-IR 未启用（无 MINIMAX_API_KEY），沿用原始提示词", task_id)
            # ---- advideo：单镜化后处理（对 LLM 或 IR 输出同样适用）----
            # Content-IR/H3 正文自带 [Shot n]/时间码/"the camera cuts to …"，ref2v 会执行成多次硬切；
            # 故做确定性单镜化后处理：切镜→连续运镜、去抖、句级去重、保真主句写进正文。
            if is_ad and enhanced and bool(getattr(settings, "advideo_ir_single_shot", True)):
                try:
                    _pp = advpostir.post_process(enhanced)
                    logger.info(
                        "任务 #%s 单镜化后处理：切镜改写 %s 处 / 去抖 %s 处 / 保留 %s 句 / 剩余 [Shot] %s / %s->%s 字符",
                        task_id, _pp["stats"]["cuts_rewritten"], _pp["stats"]["shake_rewritten"],
                        _pp["stats"]["sentences_out"], _pp["text"].count("[Shot"),
                        _pp["raw_len"], _pp["text_len"],
                    )
                    enhanced = _pp["text"]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("任务 #%s 单镜化后处理失败，沿用原文: %s", task_id, exc)
            if is_ad and enhanced and not advprompt.enhanced_ok(enhanced):
                # B2：增强结果丢了「指参考图 / 无画面文字」约束 → 弃用，回退用户原始视频提示词
                logger.warning(
                    "任务 #%s 增强结果不满足一致性约束（需同时指代参考图且禁用画面文字），已回退用户原话: %s",
                    task_id, advprompt.truncate(enhanced, 300),
                )
                enhanced = ""
            with SessionLocal() as db:
                db.get(Task, task_id).enhanced_prompt = enhanced
                db.commit()
            logger.info("任务 #%s 提示词增强完成（mode=%s 采用=%s）", task_id, mode, bool(enhanced))
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


# ---------------------------------------------------------------- ② 生成队列
async def _generate_loop() -> None:
    while True:
        # 长视频导演台独占整卡：队列里有待跑的 director 任务时，只跑它
        # （同一时刻不再领取短视频任务），避免同卡两路任务穿插。
        if settings.director_enabled and pool.has_director() and _director_pending():
            if not _director_busy():
                director_id = await asyncio.to_thread(_claim_director)
                if director_id is not None:
                    await _dispatch_director(director_id)
            await asyncio.sleep(2)
            continue
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
            .filter(
                Task.worker_url == "",
                Task.mode != "director",   # 导演台走 _claim_director（独占整卡）
                Task.mode != "image",      # 剧集设定图走 _studio_images_loop（qwen21 节点）
                or_(
                    Task.status == "generating_768p",
                    and_(Task.status == "queued", Task.enhance.is_(False)),
                ),
            )
            .order_by(Task.id)
            .first()
        )
        if task is None:
            return None
        if poison_guard.enabled() and poison_guard.is_poisoned(task):
            reason = poison_guard.poison_reason(task)
            task.status = "failed"
            task.error = f"熔断隔离：{reason}"[:500]
            task.finished_at = datetime.now()
            if task.cost > 0:
                add_credits(db, db.get(User, task.user_id), task.cost, "refund",
                            note="熔断隔离退还", task_id=task.id)
                task.cost = 0
            db.commit()
            logger.warning("任务 #%s 命中毒丸熔断名单，拒绝执行：%s", task.id, reason)
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



# ---------------------------------------------------------------- 长视频导演台
# mode=director：一次提交跑完全部段（TimelineDirector 有限分段），单卡独占，
# 与短视频生成任务互斥（有 director 待跑时生成循环不再领取短视频任务）。
DIRECTOR_TIMEOUT_SECONDS = 3600   # 长视频单次可达数十分钟，超时放宽到 1 小时
_DIRECTOR_RUNNING = False         # 进程内独占标记（后端单进程，天然全局）


def _director_pending() -> bool:
    """队列里是否还有待跑的导演台任务（queued / generating_768p 且未占位）。"""
    with SessionLocal() as db:
        return db.query(Task).filter(
            Task.mode == "director",
            Task.worker_url == "",
            Task.status.in_(("queued", "generating_768p")),
        ).first() is not None


def _director_busy() -> bool:
    return _DIRECTOR_RUNNING


def _notify_drama(task_id: int) -> None:
    """导演台任务终结后通知短剧执行器（惰性导入避免循环依赖）。

    即使通知失败也不影响任务本身：短剧 30s 巡扫会按 task 状态兜底推进。
    """
    try:
        from . import drama_runner
        drama_runner.on_task_finished(task_id)
    except Exception:
        logger.exception("短剧任务 #%s 完成通知处理失败（巡扫将兜底）", task_id)


def _claim_director() -> Optional[int]:
    """领取导演台任务（计费口径同短视频：派发即扣、失败全额退还）。"""
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(
                Task.mode == "director",
                Task.worker_url == "",
                or_(Task.status == "generating_768p", Task.status == "queued"),
            )
            .order_by(Task.id)
            .first()
        )
        if task is None:
            return None
        if poison_guard.enabled() and poison_guard.is_poisoned(task):
            reason = poison_guard.poison_reason(task)
            task.status = "failed"
            task.error = f"熔断隔离：{reason}"[:500]
            task.finished_at = datetime.now()
            if task.cost > 0:
                add_credits(db, db.get(User, task.user_id), task.cost, "refund",
                            note="熔断隔离退还", task_id=task.id)
                task.cost = 0
            db.commit()
            logger.warning("任务 #%s 命中毒丸熔断名单，拒绝执行：%s", task.id, reason)
            return None
        task_id, user_id = task.id, task.user_id
        if task.cost > 0:  # 重投任务已在首次派发时计费
            task.status = "generating_768p"
            task.worker_url = "pending"
            db.commit()
            return task_id
        user = db.get(User, user_id)
        cost = compute_director_cost(task.duration)
        if user.credits < cost:
            task.status = "failed"
            task.error = "积分不足"
            task.finished_at = datetime.now()
            db.commit()
            return None
        add_credits(db, user, -cost, "consume",
                    note=f"长视频导演台 {task.duration}s/768p", task_id=task_id)
        task.status = "generating_768p"
        task.cost = cost
        task.started_at = datetime.now()
        task.worker_url = "pending"
        db.commit()
        return task_id


async def _dispatch_director(task_id: int) -> None:
    """派发导演台任务：只认 director 标签的空闲节点（独占整卡，无兜底）。"""
    node, _pin_state = _acquire_with_pin(
        task_id, "generate", lambda: pool.acquire_director(task_id), require_director=True
    )
    if node is None:
        if _pin_state == "fail":
            poison_guard.pin_clear(task_id)
            await asyncio.to_thread(
                _fail_task, task_id,
                "节点粘滞重试超时：目标节点长时间不可用，任务判失败（不跨节点扩散；确需换节点请人工重投）",
            )
            return
        await asyncio.to_thread(_uncharge, task_id)  # 暂无专用空闲槽位：退积分回队
        return
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            pool.release(node)
            return
        task.attempts += 1
        task.worker_url = node.url
        db.commit()
    asyncio.get_event_loop().create_task(_run_director(task_id, node))


# ---------------------------------------------------------------- 导演台分块（>30s 自动拆分）
def _director_state_path(task_id: int) -> Path:
    return STAGING_DIR / f"{task_id}_director_state.json"


def _director_chunk_path(task_id: int, index: int) -> Path:
    return STAGING_DIR / f"{task_id}_director_c{int(index):02d}.mp4"


def _load_director_state(task_id: int) -> dict:
    try:
        data = json.loads(_director_state_path(task_id).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_director_state(task_id: int, state: dict) -> None:
    path = _director_state_path(task_id)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(path)


def _drop_director_chunks(task_id: int) -> None:
    """删除分块临时产物（不含成片、不含状态文件）。"""
    for path in STAGING_DIR.glob(f"{task_id}_director_c*.mp4"):
        path.unlink(missing_ok=True)


def _clear_director_state(task_id: int, keep_final: bool = False) -> None:
    """清理分块状态；失败/取消时连 staging 成片一并删除。"""
    _director_state_path(task_id).unlink(missing_ok=True)
    _drop_director_chunks(task_id)
    if not keep_final:
        (STAGING_DIR / f"{task_id}_director.mp4").unlink(missing_ok=True)


def _director_reset_current(task_id: int) -> None:
    """重试前丢掉失效的在跑分块（prompt 已随失败丢失），已完成分块保留续用。"""
    state = _load_director_state(task_id)
    if state.get("current"):
        state["current"] = None
        state["final_ready"] = False
        _save_director_state(task_id, state)


def _concat_director_chunks(task_id: int, chunks: list, out_path: Path) -> Path:
    """把分块产物裁掉块间重复交叠帧后拼接成成片。

    每个分块先统一重编码（libx264/yuv420p/AAC 48k），保证 concat 参数一致；
    非首块头部 trim_head_frames 帧与上一块重复，裁剪后再拼接。
    """
    fps = int(chunks[0]["fps"]) if chunks else 24
    parts = []
    try:
        for chunk in chunks:
            src = _director_chunk_path(task_id, chunk["chunk_index"])
            if not src.exists():
                raise RuntimeError(f"导演台分块产物缺失：{src.name}")
            dst = STAGING_DIR / f"{task_id}_director_c{int(chunk['chunk_index']):02d}_trim.mp4"
            drama_media.slice_frames(
                src, dst,
                int(chunk.get("trim_head_frames") or 0),
                int(chunk["total_frames"]),
                fps=fps,
            )
            parts.append(dst)
        drama_media.concat_clips(parts, out_path, fps=fps)
    finally:
        for path in parts:
            Path(path).unlink(missing_ok=True)
    return out_path


async def _run_director(task_id: int, node: WorkerNode) -> None:
    try:
        staging = await _generate_director(task_id, node)
        await _finalize_director(task_id, staging)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("导演台任务 #%s 生成失败", task_id)
        await asyncio.to_thread(_requeue_or_fail, task_id, "generating_768p", str(exc)[:500])
    finally:
        pool.release(node)


async def _finalize_director(task_id: int, staging: Path) -> None:
    """把 staging 成片移入 output、置 done，通知短剧执行器并清理分块状态。"""
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status != "generating_768p":
            return
        final_path = OUTPUT_DIR / f"{task_id}.mp4"
        shutil.move(str(staging), final_path)
        task.video_path = str(final_path)
        task.status = "done"
        task.finished_at = datetime.now()
        db.commit()
    logger.info("导演台任务 #%s 完成: %s", task_id, final_path)
    _clear_director_state(task_id)
    await asyncio.to_thread(_notify_drama, task_id)


def _probe_image_size(path) -> tuple:
    """用 ffprobe 取参考图真实宽高（写入 timeline_data.images，供插件按参考图尺寸构图）。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip().splitlines()
        w, h = out[0].split(",")[:2]
        return int(w), int(h)
    except Exception:  # 探测失败不阻断：留空由插件自行判断
        logger.warning("ffprobe 取参考图尺寸失败: %s", path)
        return 0, 0


async def _generate_director(task_id: int, node: WorkerNode) -> Path:
    """长视频导演台：按段清单分块提交（单块 ≤ DIRECTOR_CHUNK_SECONDS），ffmpeg 拼接成片。

    超过 30s 的任务不再一次 latent 采样跑完：按段边界拆成多块顺序提交同一节点，
    每块完成后落 staging，最后由 ffmpeg 裁掉块间重复交叠帧并拼接。
    分块进度写 staging/{task_id}_director_state.json，后端重启/重试可续跑。
    """
    global _DIRECTOR_RUNNING
    out_path = STAGING_DIR / f"{task_id}_director.mp4"
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        aspect = task.aspect_ratio
        try:
            segments = json.loads(task.segments or "[]")
        except (TypeError, ValueError):
            segments = []
        if not segments:
            raise RuntimeError("导演台任务缺少段清单(segments)")
        seed = int(segments[0].get("seed") or 0) or random.randint(0, 2 ** 63 - 1)
        # 段清单里的参考图 → 节点 input 目录（同一 upload 只传一次）
        upload_ids = []
        for seg in segments:
            for rid in seg.get("ref_image_ids") or []:
                if rid not in upload_ids:
                    upload_ids.append(rid)
        uploads = {uid: db.get(Upload, uid) for uid in upload_ids}

    plan = comfyui.plan_director_segments(segments, aspect_ratio=aspect)
    if settings.mock_comfy:
        # Mock：按段清单总时长生成 24fps 测试素材（切片按帧号对齐，多留 0.5s 防末帧缺失）
        _DIRECTOR_RUNNING = True
        try:
            await asyncio.sleep(3)
            _make_mock_video(
                out_path, duration=plan["total_frames"] / plan["fps"] + 0.5, fps=plan["fps"]
            )
        finally:
            _DIRECTOR_RUNNING = False
        logger.info(
            "导演台任务 #%s（mock）产物已落盘：%d 段 / 总 %d 帧（%.2fs）",
            task_id, len(plan["segments"]), plan["total_frames"],
            plan["total_frames"] / plan["fps"],
        )
        return out_path

    chunks = comfyui.split_director_plan(plan)
    signature = {
        "seed": seed,
        "total_frames": int(plan["total_frames"]),
        "fps": int(plan["fps"]),
        "overlap_frames": int(plan["overlap_frames"]),
        "chunk_frames": [int(c["total_frames"]) for c in chunks],
    }
    state = _load_director_state(task_id)
    if state.get("signature") != signature:
        _clear_director_state(task_id)  # 段清单/种子/分块方式变化：旧进度不可复用
        state = {"signature": signature, "completed": [], "current": None, "final_ready": False}
        _save_director_state(task_id, state)
    if state.get("final_ready") and out_path.exists():
        logger.info("导演台任务 #%s 成片已存在，跳过生成", task_id)
        return out_path

    client = comfyui.ComfyUIClient(node.url)
    images, uploaded = [], {}
    for uid, up in uploads.items():
        if up is None:
            continue
        p = Path(up.path)
        name = await client.upload_image(p)
        w, h = await asyncio.to_thread(_probe_image_size, p)
        uploaded[uid] = {"id": f"u{uid}", "file": name, "name": p.name, "width": w, "height": h}
        images.append(uploaded[uid])

    _DIRECTOR_RUNNING = True
    try:
        for chunk in chunks:
            index = int(chunk["chunk_index"])
            total = len(chunks)
            chunk_path = _director_chunk_path(task_id, index)
            if index in state.get("completed", []) and chunk_path.exists():
                continue

            current = state.get("current") or {}
            prompt_id = current.get("prompt_id") if current.get("index") == index else None
            if prompt_id:
                # 重启后续跑：节点上 prompt 已丢失则重投本块，避免空等 1 小时
                prompt_state = await client.prompt_state(prompt_id)
                if prompt_state == "missing":
                    logger.warning("导演台任务 #%s 分块 %d/%d 的 prompt 已丢失，重新提交",
                                   task_id, index + 1, total)
                    prompt_id = None
                elif prompt_state == "error":
                    raise comfyui.ComfyUIError(f"ComfyUI 执行出错（分块 {index + 1}/{total}）")
            if not prompt_id:
                workflow = comfyui.load_workflow("director")
                comfyui.inject_director(
                    workflow, chunk, images, seed + index, unet_name=node.unet_for("director")
                )
                prompt_id = await client.submit(workflow)
                state["current"] = {"index": index, "prompt_id": prompt_id}
                _save_director_state(task_id, state)
                with SessionLocal() as db:
                    db.get(Task, task_id).comfy_prompt_id = prompt_id
                    db.commit()
                logger.info(
                    "导演台任务 #%s 分块 %d/%d 已提交节点 %s：%d 帧（%.2fs）",
                    task_id, index + 1, total, node.url, chunk["total_frames"],
                    chunk["total_frames"] / chunk["fps"],
                )

            file_info = await client.wait_result(
                prompt_id, timeout_seconds=DIRECTOR_TIMEOUT_SECONDS
            )
            content = await client.fetch_file(
                file_info["filename"], file_info.get("subfolder", ""),
                file_info.get("type", "output"),
            )
            chunk_path.write_bytes(content)
            state["completed"] = sorted(set(state.get("completed", [])) | {index})
            state["current"] = None
            _save_director_state(task_id, state)
            logger.info("导演台任务 #%s 分块 %d/%d 已落盘：%s",
                        task_id, index + 1, total, chunk_path.name)
    finally:
        _DIRECTOR_RUNNING = False

    if len(chunks) == 1:
        shutil.move(str(_director_chunk_path(task_id, 0)), str(out_path))
    else:
        logger.info("导演台任务 #%s 开始拼接 %d 个分块（总 %d 帧）",
                    task_id, len(chunks), plan["total_frames"])
        await asyncio.to_thread(_concat_director_chunks, task_id, chunks, out_path)
    state["final_ready"] = True
    _save_director_state(task_id, state)
    _drop_director_chunks(task_id)
    logger.info("导演台任务 #%s 产物已落盘: %s", task_id, out_path)
    return out_path


async def _dispatch_generate(task_id: int) -> None:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        heavy = task.duration >= 10
    node, _pin_state = _acquire_with_pin(
        task_id, "generate", lambda: pool.acquire_generate(heavy, task_id), require_director=False
    )
    if node is None:
        if _pin_state == "fail":
            poison_guard.pin_clear(task_id)
            await asyncio.to_thread(
                _fail_task, task_id,
                "节点粘滞重试超时：目标节点长时间不可用，任务判失败（不跨节点扩散；确需换节点请人工重投）",
            )
            return
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
            need_upscale = settings.upscale_enabled and task.resolution in ("1k", "2k", "4k")
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
        elif task.mode == "director":
            asyncio.get_event_loop().create_task(_resume_director(task.id, node))
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
            need_upscale = settings.upscale_enabled and task.resolution in ("1k", "2k", "4k")
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


async def _resume_director(task_id: int, node: WorkerNode) -> None:
    """恢复后端重启前已派发的导演台任务。

    新版分块任务（存在 state 文件）交给 _generate_director 续跑：已完成分块跳过、
    在跑分块重挂轮询、节点上已丢失的分块重投；旧版单次提交任务沿用 prompt_id 轮询。
    """
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None or task.status != "generating_768p":
                return
            prompt_id = task.comfy_prompt_id
        if _load_director_state(task_id):
            staging = await _generate_director(task_id, node)
            await _finalize_director(task_id, staging)
            return
        if not prompt_id:
            await asyncio.to_thread(_requeue_or_fail, task_id, "generating_768p")
            return
        # ---- 旧版单次提交任务：轮询已有 prompt_id 并落盘 ----
        out_path = STAGING_DIR / f"{task_id}_director.mp4"
        client = comfyui.ComfyUIClient(node.url)
        file_info = await client.wait_result(prompt_id, timeout_seconds=DIRECTOR_TIMEOUT_SECONDS)
        content = await client.fetch_file(
            file_info["filename"], file_info.get("subfolder", ""), file_info.get("type", "output")
        )
        out_path.write_bytes(content)
        await _finalize_director(task_id, out_path)
        logger.info("孤儿导演台任务 #%s 恢复完成", task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("孤儿导演台任务 #%s 恢复失败", task_id)
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
            suffix = "4k" if task.resolution == "4k" else ("2k" if task.resolution == "2k" else "1k")
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
    本地超分池关闭时：一律失败（“超分池不可用”）并退积分（无云端回落）。"""
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(Task.status == "upscaling", Task.worker_url == "")
            .order_by(Task.resolution.desc(), Task.id)
            .first()
        )
        if task is None:
            return None
        task_id = task.id
        no_local_pool = not settings.upscale_enabled or not pool.has_upscale()
        if no_local_pool and not pool.mock:
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
            suffix = "4k" if task.resolution == "4k" else ("2k" if task.resolution == "2k" else "1k")
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
        await asyncio.to_thread(_requeue_or_fail, task_id, "upscaling", str(exc)[:500])
    finally:
        pool.release(node)


# ------------------------------------------------- 毒丸隔离辅助（POISON_GUARD）
def _fail_task(task_id: int, error: str) -> None:
    """判失败并退还积分（毒丸隔离 / 粘滞超时用）。"""
    is_director = False
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status in ("done", "failed"):
            return
        is_director = task.mode == "director"
        task.status = "failed"
        task.error = error[:500]
        task.finished_at = datetime.now()
        if task.cost > 0:
            add_credits(db, db.get(User, task.user_id), task.cost, "refund",
                        note="失败退还", task_id=task_id)
            task.cost = 0
        db.commit()
    logger.error("任务 #%s 判失败：%s", task_id, error)
    if is_director:
        _clear_director_state(task_id)  # 分块状态/临时分块一并清理
        _notify_drama(task_id)


def _acquire_with_pin(task_id: int, role: str, fallback, require_director: bool = False):
    """带节点粘滞的节点领取。返回 (node, status)。

    status = "ok"   领取成功（或无粘滞记录，走原有派发逻辑）
             "wait" 该任务被粘滞在某个节点上、该节点暂时不可用 → 稍后再派，**不换节点**
             "fail" 粘滞等待超限 → 判失败（绝不换节点扩散）
    """
    if not poison_guard.enabled():
        return fallback(), "ok"
    url = poison_guard.pin_get(task_id)
    if not url:
        return fallback(), "ok"
    node = pool.acquire_url(url, role, task_id, require_director=require_director)
    if node is not None:
        logger.info("任务 #%s 按粘滞回到原节点 %s", task_id, url)
        return node, "ok"
    if poison_guard.pin_attempts(task_id) >= poison_guard.PIN_MAX_ATTEMPTS:
        return None, "fail"
    poison_guard.pin_bump(task_id)
    return None, "wait"


# ---------------------------------------------------------------- 失败处理
def _requeue_or_fail(task_id: int, stage: str, error: str = "") -> bool:
    """失败处理（POISON_GUARD：不再让一条毒任务跨节点扩散）。

    - 运行中节点失联（宿主 OOM / 进程被杀）且属长任务 → 熔断该签名、直接判失败，**不重投**；
    - 运行中节点失联但属短任务 → 只允许在**同一节点**重试（节点粘滞），不换节点；
    - 节点存活、任务自身报错 → 沿用原逻辑（attempts < MAX_ATTEMPTS 回退重投）。
    """
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.status in ("done", "failed"):
            return False
        is_director = task.mode == "director"
        failed_url = (task.worker_url or "").rstrip("/")
        guard = poison_guard.enabled() and failed_url not in ("", "pending")
        pool_node = pool.get_node_by_url(failed_url) if failed_url else None
        node_lost = guard and (
            (pool_node is not None and not pool_node.healthy) or not poison_guard.node_alive(failed_url)
        )
        if node_lost and poison_guard.is_long(task):
            reason = f"{stage} 运行中节点 {failed_url} 失联（宿主 OOM/进程被杀）: {error}"[:300]
            poison_guard.add_poison(task, reason=reason, node=failed_url,
                                    extra={"stage": stage, "error": error[:300]})
            task.status = "failed"
            task.error = ("毒丸隔离：该任务运行中打爆节点（宿主内存耗尽/进程被杀），已熔断该任务签名，"
                          "且不会重投到其它节点；请调低段数/时长后重试")
            task.finished_at = datetime.now()
            if task.cost > 0:
                add_credits(db, db.get(User, task.user_id), task.cost, "refund",
                            note="毒丸隔离退还", task_id=task_id)
                task.cost = 0
            db.commit()
            logger.error("毒丸隔离 #%s：%s 已熔断，拒绝跨节点重投", task_id, failed_url)
            if is_director:
                _clear_director_state(task_id)
                _notify_drama(task_id)
            return False
        if task.attempts < MAX_ATTEMPTS:
            task.status = stage
            task.worker_url = ""
            db.commit()
            if is_director:
                # 失败分块的 prompt 已失效：重试时从该块重新提交，已完成分块保留
                _director_reset_current(task_id)
            if node_lost:
                poison_guard.pin_set(task_id, failed_url)
                logger.warning("任务 #%s 同节点重试（%s，第 %d 次，粘滞 %s）",
                               task_id, stage, task.attempts, failed_url)
            else:
                logger.warning("任务 #%s 回退重投（%s，已尝试 %d 次）", task_id, stage, task.attempts)
            return True
        task.status = "failed"
        task.error = error or "执行失败"
        task.finished_at = datetime.now()
        if task.cost > 0:  # 失败退还积分
            user = db.get(User, task.user_id)
            add_credits(db, user, task.cost, "refund", note="生成失败退还", task_id=task_id)
        db.commit()
        if is_director:
            _clear_director_state(task_id)  # 最终判失败：分块状态/临时分块一并清理
            _notify_drama(task_id)
    return False

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
        vmode = _video_prompt_mode(task)   # llm/content_ir=直接用增强结果（不再叠本地层）/ local=本地规则
        # B1 提示词拼装移到下面 image_paths 装配完成后（[SET] 段需要知道参考图张数）
        try:
            ref_ids = json.loads(task.ref_image_ids or "[]")
        except (TypeError, ValueError):
            ref_ids = []
        image_paths: list = []
        video_paths: list = []
        audio_paths: list = []
        # 首尾帧固定为参考图（首尾帧槽位仅允许图片）
        for img_id in (task.first_image_id, task.last_image_id):
            if img_id:
                up = db.get(Upload, img_id)
                if up:
                    image_paths.append(Path(up.path))
        # 参考区按扩展名分流（图片/视频/音频混存，保序）
        for rid in ref_ids:
            up = db.get(Upload, rid)
            if not up:
                continue
            p = Path(up.path)
            suffix = p.suffix.lower()
            if suffix in (".mp4", ".mov", ".webm", ".mkv"):
                video_paths.append(p)
            elif suffix in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
                audio_paths.append(p)
            else:
                image_paths.append(p)

        # 电商广告片：人工确认的广告图作为首个参考图（r2v 模板），商品图/参考图随其后
        if mode == "advideo" and getattr(task, "chosen_image", ""):
            image_paths.insert(0, Path(task.chosen_image))
            # R10：原始商品图紧随其后作第二锚点（材质/五金/印花校准），防止 ref2v 只靠一张图漂移
            try:
                first_pid = (json.loads(task.ad_input_ids or "[]") or [None])[0]
            except (TypeError, ValueError):
                first_pid = None
            if first_pid:
                up0 = db.get(Upload, int(first_pid))
                if up0 and Path(up0.path) not in image_paths:
                    image_paths.insert(1, Path(up0.path))
        if mode == "advideo":
            # 用户口径（10-08）：把这一套广告图**全部**作为参考图输入（r2v 上限 9 张）
            chosen_p = None
            try:
                if getattr(task, "chosen_image", ""):
                    chosen_p = Path(task.chosen_image)
            except Exception:  # noqa: BLE001
                chosen_p = None
            if chosen_p is not None:
                set_paths = sorted([p for p in chosen_p.parent.glob("*.png") if p.is_file()])
                if chosen_p.resolve() not in {p.resolve() for p in set_paths}:
                    set_paths.insert(0, chosen_p)
                seen = {p.resolve() for p in set_paths}
                others = [p for p in image_paths if p.resolve() not in seen]
                image_paths = (set_paths + others)[:9]
                set_n = len(set_paths)   # [SET] 段只覆盖这套图，不覆盖随后的商品/参考图
                logger.info("广告图任务 #%s 视频阶段参考图：整套 %d 张 + 其他 %d 张 → 实际喂 %d 张",
                            task_id, len(set_paths), len(others), len(image_paths))
            # ---- P4 视频提示词增强层（2026-10-08，0 付费纯规则）----
            # 用户给的视频提示词常是一句大白话：这里补默认动作 + 追加固定支撑条款
            # （商品一致 / 镜头稳定 / 同一人物 / 无文字），正文仍以用户原话为主。
            _set_n = locals().get("set_n", 1) or 1
            _ir_used = bool(str(task.enhanced_prompt or "").strip())
            if vmode == "local" and _ir_used:
                # A 臂显式选了本地规则：忽略云端增强结果，只用用户原话（与 0 付费口径一致）
                final_prompt = task.prompt or final_prompt
                _ir_used = False
                logger.info("任务 #%s 视频增强路由=local(A臂)：忽略已有云端增强结果，改用本地规则", task_id)
            _use_local = (vmode == "local") or not _ir_used
            if _use_local and bool(getattr(settings, "advideo_video_prompt_enhance", True)):
                _enh_v = advenhance.enhance_video_prompt(final_prompt, n_refs=_set_n, duration=duration)
                try:
                    _cdir = Path(task.chosen_image).parent if getattr(task, "chosen_image", "") else STAGING_DIR
                    (_cdir / f"{task_id}.video_enhance.json").write_text(
                        json.dumps(_enh_v, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:  # noqa: BLE001
                    logger.warning("任务 #%s 视频增强记录落盘失败（不影响出片）", task_id)
                final_prompt = str(_enh_v["text"])
                logger.info(
                    "任务 #%s 视频提示词增强(本地规则·A臂)：%d 字符（补默认=%s；剔除冲突 %d 条；整套参考图 %d 张）",
                    task_id, len(final_prompt), _enh_v["defaults_used"], len(_enh_v["conflicts"]), _set_n,
                )
            if not _use_local:
                logger.info(
                    "任务 #%s 视频增强路由=%s：直接采用云端增强结果（LLM/Content-IR，%d 字符，不再叠本地规则层）",
                    task_id, vmode, len(final_prompt or ""),
                )
            # B1：[FIDELITY] + [SET] + 正文 + [CAMERA] + [AUDIO]，SET 段按实际参考图张数生成
            final_prompt = advprompt.wrap_video_prompt(
                final_prompt, n_refs=_set_n)

    client = comfyui.ComfyUIClient(node.url)

    # 输入文件先传到该节点的 input 目录（图片走 upload_image，视频/音频走 upload_file）
    image_names = [await client.upload_image(p) for p in image_paths]
    video_names = [await client.upload_file(p) for p in video_paths]
    audio_names = [await client.upload_file(p) for p in audio_paths]

    workflow = comfyui.load_workflow(mode)
    comfyui.inject(
        workflow, mode, final_prompt, ratio, duration, image_names,
        video_names=video_names, audio_names=audio_names,
        unet_name=node.unet_for(mode),
    )
    # 2026-09-20 路线B：51:8188 / 246:8188 两个 4090 生成节点已升级 ComfyUI 0.36.0 +
    # comfy-kitchen 0.2.35，均注册 BlockSparseAttention(9600)；云端 8183(5090,0.34+kitchen0.2.34)
    # 亦支持。故**不再按能力摘除 Sol-Attn 节点**，所有生成节点统一走 Sol-Attn。
    # 回滚：取消下面一行注释，并恢复 comfyui.py 的 .bak.routeB.<ts> 备份后重启 8001。
    # workflow = await comfyui.gate_attention_nodes(workflow, node.url)

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
    if not src.exists():
        # 升级任务：回溯原始 768p 源片（来自父任务的 output 或 staging）
        with SessionLocal() as db:
            parent_id = db.get(Task, task_id).parent_task_id
        if parent_id:
            for candidate in (OUTPUT_DIR / f"{parent_id}.mp4", STAGING_DIR / f"{parent_id}_768p.mp4"):
                if candidate.exists():
                    src = candidate
                    break
    client = comfyui.ComfyUIClient(node.url)
    video_name = await client.upload_file(src)

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        tier, ratio = task.resolution, task.aspect_ratio

    # 按节点引擎选模板：engine:seedvr2 → 原生节点；默认 KSampler 管线（模板默认 3B FP16 权重）
    _engine_tpl = {"seedvr2": "upscale_7b", "seedvr2_3090": "upscale_3090", "rtx": "upscale_rtx"}
    template = _engine_tpl.get(node.engine, "upscale")
    workflow = comfyui.load_workflow(template)
    # VOSR2 L2 缓存需要源片在超分节点上的绝对路径；upload 无 subfolder，故 = <ComfyUI input>/<name>
    src_abs = f"{(settings.comfyui_input_dir or '/data/ComfyUI/input').rstrip('/')}/{video_name}"
    comfyui.inject_upscale(workflow, video_name, tier, ratio, unet_name=node.unet, src_abs_path=src_abs)

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
def _make_mock_video(out_path: Path, duration: float = 5.0, fps: int = 25) -> None:
    """Mock 模式：优先用 ffmpeg 生成带音轨的测试视频，否则写占位文件。

    director 任务需按 24fps 生成与段清单等长的素材（后处理按帧号切片）。
    """
    duration = max(0.5, float(duration))
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate={fps}:duration={duration:.3f}",
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration:.3f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(out_path),
            ],
            check=True, timeout=max(120, int(duration * 10)),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        logger.warning("ffmpeg 不可用，写入占位视频文件")
        out_path.write_bytes(b"MOCK-VIDEO-PLACEHOLDER")

# ---------------------------------------------------------------- 电商广告片：广告图阶段
from . import advimage  # noqa: E402  （放在文件末尾避免与既有导入顺序冲突）


def _claim_advideo_images() -> Optional[int]:
    """领取一个待出图的广告图任务（含后端重启遗留的 generating_images 孤儿）。"""
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(
                Task.mode == "advideo",
                Task.worker_url == "",
                Task.status.in_(("queued_images", "generating_images")),
            )
            .order_by(Task.id)
            .first()
        )
        if task is None:
            return None
        task.status = "generating_images"
        task.worker_url = "advimage"          # 占位，避免重复领取
        task.started_at = task.started_at or datetime.now()
        db.commit()
        return task.id


async def _run_advideo_images(task_id: int) -> None:
    """广告图阶段：qwen21(PE i2i) 出 N 张候选图 → status=image_ready 等人工确认。"""
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None:
                return
            scenario = (task.image_prompt or task.prompt or "").strip()
            count = task.ad_image_count or settings.advideo_image_count
            aspect_ratio = task.aspect_ratio
            try:
                input_ids = json.loads(task.ad_input_ids or "[]")
            except (TypeError, ValueError):
                input_ids = []
            n_product = int(getattr(task, "ad_product_count", 0) or 0)
            if n_product <= 0:      # 兼容旧任务：只有第 1 张算商品图
                n_product = 1
            product_paths, ref_paths = [], []
            for i, rid in enumerate(input_ids):
                up = db.get(Upload, rid)
                if up is None:
                    continue
                (product_paths if i < n_product else ref_paths).append(Path(up.path))
        if not product_paths:
            raise RuntimeError("商品图缺失（uploads 记录已删除？）")
        width, height = advimage.image_size_for(aspect_ratio)   # P1：画幅按前端比例派生
        paths = await advimage.generate_candidates(
            task_id=task_id,
            product_paths=product_paths,
            ref_paths=ref_paths,
            scenario=scenario,
            count=count,
            width=width,
            height=height,
        )
        auto_index = -1
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None or task.status != "generating_images":
                return
            task.ad_image_paths = json.dumps(paths, ensure_ascii=False)
            task.worker_url = ""
            if bool(getattr(task, "ad_auto_confirm", False)) and paths:
                # 自动模式（默认）：候选图出完即自动确认 → 直接进视频阶段，无需人工点击
                auto_index = advconfirm.apply_confirm(
                    task, int(getattr(settings, "advideo_auto_confirm_index", 0) or 0)
                )
            else:
                task.status = "image_ready"   # 手动模式：强制人工确认关卡（用户拍板 ①）
            db.commit()
        if auto_index >= 0:
            logger.info(
                "广告图任务 #%s 出图完成 %d 张（%dx%d）｜自动模式：已自动确认第 %d 张 → 进入视频阶段（无需人工点击）",
                task_id, len(paths), width, height, auto_index + 1,
            )
        else:
            logger.info("广告图任务 #%s 出图完成 %d 张（%dx%d），等待人工确认", task_id, len(paths), width, height)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("广告图任务 #%s 失败", task_id)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is not None:
                task.status = "failed"
                task.error = f"广告图阶段失败: {str(exc)[:400]}"
                task.worker_url = ""
                task.finished_at = datetime.now()
                # 出图阶段系统失败：全额退还已扣的广告图积分（仅此一处退款；删除/重生成不退）
                refund = int(getattr(task, "ad_image_cost", 0) or 0)
                if refund > 0:
                    user = db.get(User, task.user_id)
                    if user is not None:
                        add_credits(db, user, refund, "refund",
                                    note="广告图生成失败退款", task_id=task.id)
                    task.ad_image_cost = 0
                db.commit()


async def _advideo_image_loop() -> None:
    """广告图领取循环：未配置 qwen21 节点时空转（不影响既有三条循环）。"""
    while True:
        try:
            if not advimage.available():
                await asyncio.sleep(15)
                continue
            task_id = await asyncio.to_thread(_claim_advideo_images)
            if task_id is None:
                await asyncio.sleep(3)
                continue
            asyncio.get_event_loop().create_task(_run_advideo_images(task_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("广告图领取循环异常")
            await asyncio.sleep(3)


# ---------------------------------------------------------------- 剧集工坊：设定图
# 与广告图分开一条循环：同一台 qwen21 节点，但工作流/提示词/计费完全独立。
# 每个任务只出一张图（一视图一任务），失败只影响这一张。
from . import studio_image  # noqa: E402
from .billing import compute_studio_image_cost  # noqa: E402

# 进程内占位：带上 pid，重启后旧占位就成了可回收的孤儿。
# 不能再写死 "studio-image"——那样同一个任务会被同一进程立刻重复领取。
STUDIO_IMAGE_PLACEHOLDER = f"studio-image:{os.getpid()}"


def _claim_studio_images() -> Optional[int]:
    """领取一个待出的设定图任务；重投时不再重复扣费（cost>0 跳过）。

    只接两种：未占位的（worker_url 为空），以及上个进程遗留的孤儿
    （worker_url 带 studio-image: 前缀但不是当前进程）。
    """
    with SessionLocal() as db:
        task = (
            db.query(Task)
            .filter(
                Task.mode == "image",
                Task.status.in_(("queued", "generating_images")),
                or_(
                    Task.worker_url == "",
                    and_(
                        Task.worker_url.like("studio-image:%"),
                        Task.worker_url != STUDIO_IMAGE_PLACEHOLDER,
                    ),
                ),
            )
            .order_by(Task.id)
            .first()
        )
        if task is None:
            return None

        if task.cost <= 0:
            cost = compute_studio_image_cost(1)
            user = db.get(User, task.user_id)
            if cost and (user is None or user.credits < cost):
                task.status = "failed"
                task.error = "积分不足"
                task.finished_at = datetime.now()
                db.commit()
                return None
            if cost and user is not None:
                add_credits(db, user, -cost, "consume", note="剧集设定图", task_id=task.id)
            task.cost = cost

        task.status = "generating_images"
        task.worker_url = STUDIO_IMAGE_PLACEHOLDER
        task.started_at = task.started_at or datetime.now()
        db.commit()
        return task.id


async def _run_studio_images(task_id: int) -> None:
    """出一张设定图或关键帧：调 qwen21 工作流 → 落盘 → 写对应业务表。"""
    from ..studio.models import StudioAsset, StudioAssetView, StudioJob

    payload: dict = {}
    try:
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None:
                return
            try:
                payload = json.loads(task.segments or "{}")
            except (TypeError, ValueError):
                payload = {}
        if not payload.get("prompt"):
            raise RuntimeError("任务缺少出图提示词（segments 为空）")

        if payload.get("target") == "keyframe":
            await _run_studio_keyframe(task_id, payload)
            return

        ref_paths = [Path(p) for p in payload.get("refs") or [] if Path(p).exists()]
        seed = payload.get("seed")
        result = await studio_image.generate_to_dir(
            project_id=int(payload["project_id"]),
            ref_id=str(payload["ref_id"]),
            view=str(payload["view"]),
            version=int(payload.get("version") or 1),
            prompt=str(payload["prompt"]),
            negative=str(payload.get("negative") or ""),
            seed=int(seed) if seed is not None else None,
            ref_paths=ref_paths,
        )

        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is None:
                return
            asset = db.get(StudioAsset, int(payload["asset_id"]))
            if asset is not None:
                version = int(payload.get("version") or 1)
                row = (
                    db.query(StudioAssetView)
                    .filter(
                        StudioAssetView.asset_id == asset.id,
                        StudioAssetView.view == str(payload["view"]),
                        StudioAssetView.version == version,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = StudioAssetView(
                        asset_id=asset.id, view=str(payload["view"]), version=version
                    )
                    db.add(row)
                row.path = result["path"]
                row.sha256 = result["sha256"]
                row.model = "qwen"
                row.prompt = str(payload["prompt"])
                row.refs = json.dumps(payload.get("refs") or [], ensure_ascii=False)
                row.confirmed = False
                asset.status = "imaging"

                job = (
                    db.query(StudioJob)
                    .filter(StudioJob.task_id == task_id)
                    .one_or_none()
                )
                if job is not None:
                    job.status = "done"
                    job.cost = int(task.cost or 0)
                    job.error = ""

            task.status = "done"
            task.video_path = result["path"]
            task.finished_at = datetime.now()
            task.worker_url = ""
            db.commit()
        logger.info(
            "剧集设定图任务 #%s 完成：%s → %s",
            task_id, payload.get("view"), result.get("path"),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("剧集设定图任务 #%s 失败", task_id)
        with SessionLocal() as db:
            task = db.get(Task, task_id)
            if task is not None:
                task.status = "failed"
                task.error = f"设定图生成失败: {str(exc)[:400]}"
                task.worker_url = ""
                task.finished_at = datetime.now()
                # 系统失败全额退还（重试成功则不再退）
                refund = int(task.cost or 0)
                if refund > 0:
                    user = db.get(User, task.user_id)
                    if user is not None:
                        add_credits(db, user, refund, "refund", note="剧集设定图失败退款", task_id=task.id)
                    task.cost = 0
                try:
                    from ..studio.models import StudioJob, StudioKeyframe

                    job = db.query(StudioJob).filter(StudioJob.task_id == task_id).one_or_none()
                    if job is not None:
                        job.status = "failed"
                        job.error = str(exc)[:400]
                    # 关键帧失败回退到 missing，前端可重试；不保留“排队中”的假象
                    if payload.get("target") == "keyframe" and payload.get("segment_id"):
                        kf = (
                            db.query(StudioKeyframe)
                            .filter(
                                StudioKeyframe.episode_id == int(payload["episode_id"]),
                                StudioKeyframe.segment_id == str(payload["segment_id"]),
                                StudioKeyframe.cut_index == int(payload["cut_index"]),
                            )
                            .one_or_none()
                        )
                        if kf is not None:
                            kf.status = "missing"
                except Exception:  # noqa: BLE001 业务表写不进去不影响任务退款
                    logger.warning("回写 studio_jobs 失败（任务 #%s）", task_id)
                db.commit()


async def _run_studio_keyframe(task_id: int, payload: dict) -> None:
    """出一张关键帧：9:16 改图 → 落盘 → 写 studio_keyframes。失败抛给外层统一退款。"""
    from ..studio.models import StudioJob, StudioKeyframe

    out_path = Path(str(payload.get("out_path") or ""))
    if not str(out_path):
        raise RuntimeError("关键帧任务缺少落盘路径")
    ref_paths = [Path(p) for p in payload.get("refs") or [] if Path(p).exists()]
    result = await studio_image.generate_image(
        prompt=str(payload["prompt"]),
        ratio=str(payload.get("ratio") or "9:16"),
        ref_paths=ref_paths,
        prefix=f"studio/p{payload['project_id']}/{payload['segment_id']}_c{payload['cut_index']}",
        out_path=out_path,
    )

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            return
        row = (
            db.query(StudioKeyframe)
            .filter(
                StudioKeyframe.episode_id == int(payload["episode_id"]),
                StudioKeyframe.segment_id == str(payload["segment_id"]),
                StudioKeyframe.cut_index == int(payload["cut_index"]),
            )
            .one_or_none()
        )
        if row is not None:
            row.path = result["path"]
            row.sha256 = result["sha256"]
            row.prompt = str(payload["prompt"])
            row.refs = json.dumps(payload.get("refs") or [], ensure_ascii=False)
            row.status = "ready"

        job = db.query(StudioJob).filter(StudioJob.task_id == task_id).one_or_none()
        if job is not None:
            job.status = "done"
            job.cost = int(task.cost or 0)
            job.error = ""

        task.status = "done"
        task.video_path = result["path"]
        task.finished_at = datetime.now()
        task.worker_url = ""
        db.commit()
    logger.info(
        "剧集关键帧任务 #%s 完成：%s c%s → %s",
        task_id, payload.get("segment_id"), payload.get("cut_index"), result.get("path"),
    )


async def _studio_images_loop() -> None:
    """设定图领取循环：节点未配时空转，不影响其它循环。"""
    while True:
        try:
            if not studio_image.available():
                await asyncio.sleep(15)
                continue
            task_id = await asyncio.to_thread(_claim_studio_images)
            if task_id is None:
                await asyncio.sleep(3)
                continue
            asyncio.get_event_loop().create_task(_run_studio_images(task_id))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("剧集设定图领取循环异常")
            await asyncio.sleep(3)

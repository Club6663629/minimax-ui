"""视频任务路由：提交 / 列表 / 详情 / 重试 / 升级 / 删除 / 价格。"""
import json
import random
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..auth import get_advideo_access, get_current_user
from ..config import OUTPUT_DIR, STAGING_DIR, settings
from ..database import get_db
from ..models import CreditLog, Task, Upload, User
from ..schemas import (
    AdvideoConfirmIn,
    AdvideoCreateIn,
    PackageOut,
    PricingOut,
    TaskOut,
    UpgradeIn,
    VideoCreateIn,
)
from ..services import advimage, comfyui
from ..services.billing import (
    PACKAGES,
    compute_cost,
    compute_director_cost,
    compute_upgrade_cost,
)
from .serialize import serialize_task

router = APIRouter(prefix="/api/videos", tags=["videos"])

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _ref_counts(db: Session, ref_ids: list[int]) -> tuple[int, int, int]:
    """按参考上传文件后缀统计 图片/视频/音频 数量（未知文件按图片计）。"""
    video = audio = 0
    for rid in ref_ids:
        up = db.get(Upload, rid)
        if up is None:
            continue
        suffix = Path(up.path).suffix.lower()
        if suffix in _VIDEO_SUFFIXES:
            video += 1
        elif suffix in _AUDIO_SUFFIXES:
            audio += 1
    return len(ref_ids) - video - audio, video, audio


@router.get("/pricing", response_model=PricingOut)
def pricing():
    return PricingOut(
        signup_bonus=settings.signup_bonus,
        cost_768p_5s=settings.cost_768p_5s,
        cost_768p_8s=settings.cost_768p_8s,
        cost_768p_10s=settings.cost_768p_10s,
        cost_768p_15s=settings.cost_768p_15s,
        cost_1k_extra=settings.cost_1k_extra,
        cost_2k_extra=settings.cost_2k_extra,
        cost_4k_extra=settings.cost_4k_extra,
        cloud_enabled=settings.cloud_enabled,
        upscale_enabled=settings.upscale_enabled,
        packages=[PackageOut(**p) for p in PACKAGES],
    )


def _create_director(body: VideoCreateIn, user: User, db: Session):
    """长视频导演台（mode=director）：一次提交跑完全部段（本轮只做后端/API）。

    段清单写入 tasks.segments（每段 frames/start_frame/end_frame/seed/提示词/参考图），
    帧数吸附 5+17n、段间交叠 39 帧（=5+17*2，插件合法交叠集 0/1/5+17n 的默认值）。
    """
    if not settings.director_enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "长视频导演台未启用（DIRECTOR_ENABLED=false，拍板后再开启）",
        )
    segs = [s.model_dump() for s in body.segments] or [
        {"prompt": body.prompt.strip(), "duration": float(body.duration), "ref_image_ids": []}
    ]
    try:
        plan = comfyui.plan_director_segments(segs, aspect_ratio=body.aspect_ratio)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    seed = random.randint(0, 2 ** 63 - 1)
    for seg in plan["segments"]:
        seg["seed"] = seed
    total_seconds = plan["total_frames"] / plan["fps"]
    cost = compute_director_cost(total_seconds)
    if user.credits < cost:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"积分不足，长视频 {total_seconds:.1f}s 需 {cost} 积分",
        )
    task = Task(
        user_id=user.id,
        mode="director",
        prompt=(body.prompt.strip() or " / ".join(s["prompt"][:60] for s in plan["segments"]))[:2000],
        aspect_ratio=body.aspect_ratio,
        duration=int(round(total_seconds)),
        resolution="768p",
        enhance=False,               # 导演台不做云端提示词增强（不产生付费调用）
        scene=body.scene,
        segments=json.dumps(plan["segments"], ensure_ascii=False),
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return serialize_task(task, db=db)


@router.post("", response_model=TaskOut)
def create_video(
    body: VideoCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 长视频导演台：独立分支（段清单 → 帧窗口 → 一次提交跑完全部段）
    if body.mode == "director":
        return _create_director(body, user, db)
    # 模式与输入图校验
    if body.mode == "flf2v" and not (body.first_image_id or body.last_image_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "首尾帧模式需要至少上传首帧或尾帧图片")
    if body.mode == "r2v" and not body.ref_image_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "全能参考模式需要上传参考图")
    # 参考区总数 ≤9，其中视频 ≤3、音频 ≤3（图片占余量）
    if body.mode == "r2v":
        if len(body.ref_image_ids) > 9:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "参考资料总数最多 9 个")
        _, v_cnt, a_cnt = _ref_counts(db, body.ref_image_ids)
        if v_cnt > 3:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "参考视频最多 3 个")
        if a_cnt > 3:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "参考音频最多 3 个")
    # 1K/2K 依赖本地超分池（云端仅保留 Context-IR 增强，不再云端回落）
    if body.resolution in ("1k", "2k", "4k"):
        if not settings.upscale_enabled:
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
        scene=body.scene,
        first_image_id=body.first_image_id if body.mode == "flf2v" else None,
        last_image_id=body.last_image_id if body.mode == "flf2v" else None,
        ref_image_ids=json.dumps(body.ref_image_ids) if body.mode == "r2v" else "",
        status="queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return serialize_task(task, db=db)


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
    return [serialize_task(t, db=db) for t in tasks]


@router.get("/{task_id}", response_model=TaskOut)
def get_video(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    return serialize_task(task, db=db)


@router.post("/{task_id}/retry", response_model=TaskOut)
def retry_video(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = _get_owned_task(task_id, user, db)
    if task.status != "failed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅失败任务可重试")
    # 导演台按总时长折算计费（5s 块 × 5s 档单价），短视频按档位计费
    cost = (
        compute_director_cost(task.duration)
        if task.mode == "director"
        else compute_cost(task.duration, task.resolution)
    )
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
    return serialize_task(task, db=db)


@router.post("/{task_id}/upgrade", response_model=TaskOut)
def upgrade_video(
    task_id: int,
    body: UpgradeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对已完成 768p 视频发起高清升级（1K/2K）。"""
    task = _get_owned_task(task_id, user, db)
    if task.status != "done":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅已完成任务可升级")
    if task.resolution != "768p":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "仅 768p 视频支持高清升级")
    if not settings.upscale_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "本地超分池未启用，暂不支持该分辨率档位")
    # 检查是否已有同档位的升级任务（避免重复提交）
    existing = (
        db.query(Task)
        .filter(
            Task.parent_task_id == task_id,
            Task.upscale_target == body.resolution,
            Task.status.in_(("queued", "upscaling", "done")),
        )
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"该视频已有 {body.resolution} 升级任务（#{existing.id}）")
    # 检查 768p 源文件存在
    src_exists = any([
        (STAGING_DIR / f"{task_id}_768p.mp4").exists(),
        (OUTPUT_DIR / f"{task_id}.mp4").exists(),
    ])
    if not src_exists:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "768p 源文件不存在，无法升级")
    # 计费
    cost = compute_upgrade_cost(body.resolution)
    if user.credits < cost:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, f"积分不足，升级需 {cost} 积分，请先充值")
    # 创建升级任务
    upgrade_task = Task(
        user_id=user.id,
        mode=task.mode,
        prompt=task.prompt,
        enhanced_prompt=task.enhanced_prompt or "",
        aspect_ratio=task.aspect_ratio,
        duration=task.duration,
        resolution=body.resolution,
        enhance=False,
        parent_task_id=task_id,
        upscale_target=body.resolution,
        status="upscaling",
        cost=cost,
    )
    user.credits -= cost
    db.add(upgrade_task)
    db.commit()
    db.refresh(upgrade_task)
    return serialize_task(upgrade_task, db=db)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除任务（含其高清升级子任务）。

    credit_logs.task_id 外键（fk_2）没有 ON DELETE CASCADE，直接删任务会抛
    MySQL 1451 变成 500；这里先把这些流水的 task_id 置 NULL（保留财务台账与金额），
    再在同一事务内删除任务本身及其子任务。
    """
    task = _get_owned_task(task_id, user, db)

    # 待删集合：自身 + 以 parent_task_id 指向它的子任务（含多级）
    ids = [task.id]
    frontier = [task.id]
    while frontier:
        rows = db.query(Task.id).filter(Task.parent_task_id.in_(frontier)).all()
        kids = [r[0] for r in rows if r[0] not in ids]
        ids.extend(kids)
        frontier = kids

    targets = db.query(Task).filter(Task.id.in_(ids)).all()

    running = [t.id for t in targets if t.status in ("enhancing", "generating_768p", "upscaling")]
    if running:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "任务执行中，暂不可删除（#" + "、#".join(str(i) for i in running) + "）",
        )

    try:
        # 财务台账保留：仅解除对任务的引用（task_id 可空），解除外键阻塞
        db.query(CreditLog).filter(CreditLog.task_id.in_(ids)).update(
            {CreditLog.task_id: None}, synchronize_session=False
        )
        # 子任务先删，再删主任务
        for t in sorted(targets, key=lambda x: 0 if x.id == task.id else 1):
            db.delete(t)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "删除任务失败：" + type(exc).__name__ + ": " + str(exc),
        ) from exc


def _get_owned_task(task_id: int, user: User, db: Session) -> Task:
    task = db.get(Task, task_id)
    if task is None or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return task

# ---------------------------------------------------------------- 电商广告片（advideo）
@router.get("/advideo/status")
def advideo_status(_user: User = Depends(get_advideo_access)):
    """广告图阶段是否就绪（前端据此禁用入口/提示原因）。

    advideo9：额外下发「生视频提示词增强路由」默认值与可选项，前端据此渲染增强开关。
    2026-10-09：默认主方案改为 llm（qwen3.8-flash），Content-IR 降为备选。
    """
    st = advimage.status()
    default_mode = str(getattr(settings, "advideo_video_prompt_mode", "llm") or "llm").strip().lower()
    if default_mode not in ("llm", "content_ir", "local"):
        default_mode = "llm"
    st.update({
        "video_prompt_default_mode": default_mode,
        "video_prompt_modes": [
            {"value": "llm", "label": "Qwen-Flash 增强"},
            {"value": "content_ir", "label": "Content-IR 电商"},
            {"value": "local", "label": "本地规则"},
        ],
        "video_prompt_enhance_enabled": bool(getattr(settings, "advideo_video_prompt_enhance", True)),
    })
    return st


@router.post("/advideo", response_model=TaskOut)
def create_advideo(
    body: AdvideoCreateIn,
    user: User = Depends(get_advideo_access),
    db: Session = Depends(get_db),
):
    """电商广告片：商品图(+可选参考图) + 一句话场景 → N 张候选广告图（待人工确认）。

    计费口径与 r2v 视频一致，**在人工确认广告图后、视频阶段领取时扣**（复用生成循环的
    领取计费）；用户只看不确认时不扣积分。
    """
    if not settings.advideo_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "电商广告片未启用")
    if not advimage.available():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "广告图节点未就绪（qwen21 未切 to-qwen21，或 ADVIDEO_IMAGE_WORKER 未配置）",
        )
    input_ids = list(body.product_image_ids) + list(body.ref_image_ids)
    for rid in input_ids:
        up = db.get(Upload, rid)
        if up is None or (up.user_id != user.id and user.role != "admin"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"素材 {rid} 不存在")
    if body.resolution in ("1k", "2k", "4k") and not settings.upscale_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "本地超分池未启用，暂不支持该分辨率档位")
    cost = compute_cost(body.duration, body.resolution)
    if user.credits < cost:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"积分不足：确认广告图后将生成 {body.duration}s/{body.resolution} 视频，需 {cost} 积分",
        )
    task = Task(
        user_id=user.id,
        mode="advideo",
        prompt=(body.video_prompt.strip() or body.prompt.strip())[:2000],  # 视频提示词（独立框）
        image_prompt=body.prompt.strip(),                                  # 图像提示词（隔离）
        aspect_ratio=body.aspect_ratio,
        duration=body.duration,
        resolution=body.resolution,
        enhance=body.enhance,
        scene=body.scene,
        ref_image_ids=json.dumps(input_ids),
        ad_input_ids=json.dumps(input_ids),
        ad_image_count=body.image_count,
        ad_product_count=len(body.product_image_ids),
        video_prompt_mode=(body.video_prompt_mode or ""),   # advideo9 生视频增强路由（空=跟随全局默认）
        status="queued_images",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return serialize_task(task, db=db)


@router.post("/advideo/{task_id}/regenerate", response_model=TaskOut)
def regenerate_advideo(
    task_id: int,
    user: User = Depends(get_advideo_access),
    db: Session = Depends(get_db),
):
    """重新生成候选广告图：以既有任务参数为源新建一条任务。

    - 图像阶段不计费，因此旧任务（尚未进入视频阶段）连记录一并丢弃，避免页面上遗留「待确认」任务；
    - 参数（商品图/参考图/场景/比例/片长/清晰度/增强路由/张数）全部取自服务端任务，
      前端不需要回填任何内存状态（修复「重新生成却提示上传商品图」）。
    """
    task = _get_owned_task(task_id, user, db)
    if task.mode != "advideo":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非电商广告片任务")
    if task.status not in ("queued_images", "generating_images", "image_ready"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "广告片已进入生成阶段（已计费），如需重做请新建广告片",
        )
    if not settings.advideo_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "电商广告片未启用")
    if not advimage.available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "广告图节点未就绪，请稍后再试")

    clone = Task(
        user_id=user.id,
        mode="advideo",
        prompt=task.prompt,
        image_prompt=task.image_prompt,
        aspect_ratio=task.aspect_ratio,
        duration=task.duration,
        resolution=task.resolution,
        enhance=task.enhance,
        scene=task.scene,
        ref_image_ids=task.ref_image_ids,
        ad_input_ids=task.ad_input_ids,
        ad_image_count=task.ad_image_count,
        ad_product_count=task.ad_product_count,
        video_prompt_mode=task.video_prompt_mode,
        status="queued_images",
    )

    # 旧任务树（自身 + 高清子任务）一并清理；credit_logs.task_id 外键先解除引用
    ids = [task.id]
    frontier = [task.id]
    while frontier:
        rows = db.query(Task.id).filter(Task.parent_task_id.in_(frontier)).all()
        kids = [r[0] for r in rows if r[0] not in ids]
        ids.extend(kids)
        frontier = kids
    targets = db.query(Task).filter(Task.id.in_(ids)).all()

    db.add(clone)
    try:
        db.query(CreditLog).filter(CreditLog.task_id.in_(ids)).update(
            {CreditLog.task_id: None}, synchronize_session=False
        )
        for t in targets:
            db.delete(t)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "重新生成失败：" + type(exc).__name__ + ": " + str(exc),
        ) from exc
    db.refresh(clone)
    return serialize_task(clone, db=db)


@router.post("/{task_id}/confirm-image", response_model=TaskOut)
def confirm_advideo_image(
    task_id: int,
    body: AdvideoConfirmIn,
    user: User = Depends(get_advideo_access),
    db: Session = Depends(get_db),
):
    """人工确认广告图（强制关卡）：选定第 image_index 张后才进入视频阶段。"""
    task = _get_owned_task(task_id, user, db)
    if task.mode != "advideo":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非电商广告片任务")
    if task.status != "image_ready":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"当前状态 {task.status} 不可确认广告图"
        )
    try:
        paths = json.loads(task.ad_image_paths or "[]")
    except (TypeError, ValueError):
        paths = []
    if not (0 <= body.image_index < len(paths)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "选择的广告图不存在")
    task.chosen_image = paths[body.image_index]
    task.chosen_index = body.image_index
    if body.video_prompt and body.video_prompt.strip():
        task.prompt = body.video_prompt.strip()[:2000]
    if body.enhance is not None:
        task.enhance = body.enhance
    if body.video_prompt_mode:
        # advideo9：生视频增强路由（前端增强开关：llm 主方案 / content_ir 备选 / local 本地）
        task.video_prompt_mode = body.video_prompt_mode
        if body.video_prompt_mode == "local":
            task.enhance = True   # A 臂也要进增强阶段（跳过云端 IR，仅本地规则，0 付费）
    task.status = "queued"          # 复用既有生成循环（enhance → 768p → 超分）
    task.error = ""
    task.worker_url = ""
    task.started_at = None
    task.finished_at = None
    db.commit()
    db.refresh(task)
    return serialize_task(task, db=db)

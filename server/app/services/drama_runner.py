"""短剧执行器：整集提交、单镜返修、后处理、巡扫对账、回滚与交付。

并发模型：
- 一个集同一时刻只允许 1 个活跃 run，靠 drama_episodes.active_run_id 的条件 UPDATE 抢占；
- worker 全局串行跑 director 任务，后处理在本进程线程内完成（ffmpeg）；
- 30s 巡扫负责：queued+task 已完成/失败推进、postprocess 超时重置、active_run_id 清空。

幂等：
- 版本号固定取 run.id（重跑同一 run 得到同一版本号）；
- 后处理先写 versioned 文件，最后单事务切 current 指针；
- 稳定路径 clips/shot_XXX.mp4 与 final/episode.mp4 是派生数据，事务提交后重建。
"""
import hashlib
import json
import logging
import os
import random
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import DRAMA_DIR, settings
from ..database import SessionLocal
from ..models import (
    DramaArtifact, DramaCharacter, DramaEpisode, DramaProject, DramaRun, DramaScene,
    DramaShot, Task, Upload, User,
)
from . import comfyui, drama_composer as composer, drama_media as media
from .billing import compute_director_cost

logger = logging.getLogger(__name__)

DRAMA_FPS = composer.DRAMA_FPS
POSTPROCESS_TIMEOUT_SECONDS = 600
SWEEP_INTERVAL_SECONDS = 30


class DramaError(Exception):
    """带 HTTP 语义的业务错误（router 直接转 HTTPException）。"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ---------------------------------------------------------------- 路径
def episode_root(episode: DramaEpisode) -> Path:
    return DRAMA_DIR / str(episode.project_id) / str(episode.id)


def clip_version_path(episode: DramaEpisode, shot_no: int, version: int) -> Path:
    return episode_root(episode) / "clips" / "versions" / f"shot_{shot_no:03d}" / f"v{version}.mp4"


def clip_stable_path(episode: DramaEpisode, shot_no: int) -> Path:
    return episode_root(episode) / "clips" / f"shot_{shot_no:03d}.mp4"


def final_version_path(episode: DramaEpisode, version: int) -> Path:
    return episode_root(episode) / "final" / "versions" / f"episode_v{version}.mp4"


def final_stable_path(episode: DramaEpisode) -> Path:
    return episode_root(episode) / "final" / "episode.mp4"


def sheet_path(episode: DramaEpisode, version: int) -> Path:
    return episode_root(episode) / "sheet" / f"episode_v{version}.jpg"


def anchor_path(episode: DramaEpisode, shot_no: int, side: str) -> Path:
    return episode_root(episode) / "anchors" / f"shot_{shot_no:03d}_{side}.png"


def _snapshot(episode: DramaEpisode, run_id: int, payload: dict) -> None:
    try:
        target = episode_root(episode) / "runs" / f"run_{run_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("run #%s 快照写入失败（不阻塞）", run_id)


def _atomic_copy(src: Path, dst: Path) -> None:
    """硬链优先、复制兜底，最后原子替换稳定路径。"""
    if not src.exists():
        raise media.MediaError(f"源文件缺失：{src.name}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex[:6]}.tmp")
    try:
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def current_clip(db: Session, shot_id: int) -> Optional[DramaArtifact]:
    return (
        db.query(DramaArtifact)
        .filter(DramaArtifact.shot_id == shot_id, DramaArtifact.kind == "clip",
                DramaArtifact.is_current.is_(True))
        .order_by(DramaArtifact.id.desc())
        .first()
    )


def current_final(db: Session, episode: DramaEpisode) -> Optional[DramaArtifact]:
    if episode.final_artifact_id:
        artifact = db.get(DramaArtifact, episode.final_artifact_id)
        if artifact is not None:
            return artifact
    return (
        db.query(DramaArtifact)
        .filter(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "final",
                DramaArtifact.is_current.is_(True))
        .order_by(DramaArtifact.id.desc())
        .first()
    )


def current_sheet(db: Session, episode: DramaEpisode) -> Optional[DramaArtifact]:
    return (
        db.query(DramaArtifact)
        .filter(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "sheet",
                DramaArtifact.is_current.is_(True))
        .order_by(DramaArtifact.id.desc())
        .first()
    )


# ---------------------------------------------------------------- 提交流程
def _latest_full_run(db: Session, episode: DramaEpisode) -> Optional[DramaRun]:
    return (
        db.query(DramaRun)
        .filter(DramaRun.episode_id == episode.id, DramaRun.mode == "full")
        .order_by(DramaRun.id.desc())
        .first()
    )


def _find_reusable_run(db: Session, episode: DramaEpisode, spec_hash: str) -> Optional[DramaRun]:
    run = (
        db.query(DramaRun)
        .filter(DramaRun.episode_id == episode.id, DramaRun.mode == "full",
                DramaRun.status == "done", DramaRun.spec_hash == spec_hash)
        .order_by(DramaRun.id.desc())
        .first()
    )
    if run is None:
        return None
    task = db.get(Task, run.task_id)
    final = current_final(db, episode)
    if task is None or task.status != "done":
        return None
    if final is None or not Path(final.path).exists():
        return None
    if final.spec_hash != spec_hash:
        return None
    return run


def submit_full(db: Session, episode: DramaEpisode, user: User, force: bool = False) -> dict:
    """提交整集生成；返回 {reused, run_id, cost}。"""
    if not settings.drama_enabled or not settings.director_enabled:
        raise DramaError(400, "短剧功能未开启（需同时开启 DRAMA_ENABLED 与 DIRECTOR_ENABLED）")
    if episode.status == "rendering" or episode.active_run_id:
        raise DramaError(409, f"本集正在生成中（run #{episode.active_run_id}），请等待完成")
    if episode.status == "done":
        raise DramaError(409, "本集已交付，请先「重新编辑」再生成")

    checklist = composer.validate_episode(db, episode, user)
    if not checklist["ready"]:
        first = checklist["issues"][0]
        raise DramaError(400, first["message"] if first else "就绪检查未通过")

    spec_hash = composer.compute_spec_hash(db, episode)
    episode.spec_hash = spec_hash
    if not force:
        reusable = _find_reusable_run(db, episode, spec_hash)
        if reusable is not None:
            db.commit()
            return {"reused": True, "run_id": reusable.id, "cost": 0}

    try:
        plan = composer.build_plan(db, episode)
    except (ValueError, AssertionError, KeyError) as exc:
        raise DramaError(400, f"分镜排版失败：{exc}") from exc

    cost = composer.estimate_cost(plan)
    if user.credits < cost:
        raise DramaError(402, f"本集预计 {cost} 积分，当前余额 {user.credits}")

    project = db.get(DramaProject, episode.project_id)
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no)
        .all()
    )
    plan["mode"] = "full"
    for seg in plan["segments"]:
        seg["seed"] = random.randint(0, 2 ** 63 - 1)
    duration_int = max(1, round(plan["total_frames"] / DRAMA_FPS))
    summary = f"短剧#{episode.id} 第{episode.episode_no}集 整集 {len(shots)} 镜"

    task = Task(
        user_id=user.id,
        mode="director",
        prompt=summary[:2000],
        aspect_ratio=project.aspect_ratio if project else "16:9",
        duration=duration_int,
        resolution="768p",
        enhance=False,
        scene="drama",
        segments=json.dumps(plan["segments"], ensure_ascii=False),
        status="queued",
    )
    db.add(task)
    db.flush()
    run = DramaRun(
        episode_id=episode.id,
        task_id=task.id,
        mode="full",
        status="queued",
        shot_ids="[]",
        plan=json.dumps(plan, ensure_ascii=False),
        spec_hash=spec_hash,
        cost=0,
    )
    db.add(run)
    db.flush()

    claimed = db.execute(
        update(DramaEpisode)
        .where(DramaEpisode.id == episode.id, DramaEpisode.active_run_id.is_(None))
        .values(active_run_id=run.id, status="rendering", last_error="")
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        db.rollback()
        raise DramaError(409, "本集已有进行中的生成任务，请等待完成")
    db.commit()
    _snapshot(episode, run.id, plan)
    logger.info(
        "短剧集 #%s 提交整集 run #%s：%d 镜 / %d 帧 / 预估 %d 积分",
        episode.id, run.id, len(shots), plan["total_frames"], cost,
    )
    return {"reused": False, "run_id": run.id, "cost": cost}


def _save_anchor_upload(db: Session, image_path: Path, user_id: int) -> int:
    content = image_path.read_bytes()
    upload = Upload(
        user_id=user_id,
        slot="reference",
        filename=image_path.name,
        path=str(image_path),
        md5=hashlib.md5(content).hexdigest(),
        size=len(content),
    )
    db.add(upload)
    db.flush()
    return upload.id


def _pick_prev_status(db: Session, shot: DramaShot) -> str:
    if shot.status != "rejected":
        return shot.status
    last = (
        db.query(DramaRun)
        .filter(DramaRun.mode == "patch", DramaRun.episode_id == shot.episode_id)
        .order_by(DramaRun.id.desc())
        .first()
    )
    if last is not None:
        try:
            prev = json.loads(last.plan or "{}").get("prev_status")
            if prev:
                return prev
        except (TypeError, ValueError):
            pass
    return "rendered"


def submit_patch(db: Session, episode: DramaEpisode, shot: DramaShot, reason: str,
                 anchor: bool = True) -> dict:
    """提交单镜返修（L1 图锚）；返回 {run_id, cost, window_frames}。"""
    if not settings.drama_enabled or not settings.director_enabled:
        raise DramaError(400, "短剧功能未开启（需同时开启 DRAMA_ENABLED 与 DIRECTOR_ENABLED）")
    if episode.status != "review":
        raise DramaError(409, "当前集不在审片阶段，无法返修")
    if episode.active_run_id:
        raise DramaError(409, f"本集正在生成中（run #{episode.active_run_id}），请等待完成")
    if not (reason or "").strip():
        raise DramaError(409, "请填写返修原因（会写入重采样提示词）")

    full_run = _latest_full_run(db, episode)
    if full_run is None or full_run.status != "done" or full_run.spec_hash != episode.spec_hash:
        raise DramaError(409, "分镜已修改，返修前请重新生成整集")
    try:
        full_plan = json.loads(full_run.plan or "{}")
    except (TypeError, ValueError):
        raise DramaError(409, "整集排版快照缺失，请重新生成整集") from None
    window = next((w for w in full_plan.get("windows", []) if w.get("shot_id") == shot.id), None)
    if window is None:
        raise DramaError(409, "该镜头不在最近一次整集排版中，请重新生成整集")

    target_clip = current_clip(db, shot.id)
    if target_clip is None or not Path(target_clip.path).exists():
        raise DramaError(409, "该镜头还没有成片，请先整集生成")

    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no)
        .all()
    )
    pos = next((i for i, s in enumerate(shots) if s.id == shot.id), None)
    prev_shot = shots[pos - 1] if pos is not None and pos > 0 else None
    next_shot = shots[pos + 1] if pos is not None and pos + 1 < len(shots) else None
    prev_clip = current_clip(db, prev_shot.id) if prev_shot else None
    next_clip = current_clip(db, next_shot.id) if next_shot else None
    if anchor and prev_clip is None and next_clip is None:
        raise DramaError(409, "相邻镜头缺少成片，无法建立首尾锚点")
    if not anchor and prev_clip is None and next_clip is None:
        logger.info("返修镜 #%s 关闭图锚且无邻镜，仅文本重采样", shot.shot_no)

    project = db.get(DramaProject, episode.project_id)
    owner_id = project.user_id if project else episode.project_id
    prev_upload_id = next_upload_id = None
    if anchor:
        if prev_clip is not None:
            p = anchor_path(episode, shot.shot_no, "prev")
            media.extract_frame(prev_clip.path, max(0, int(prev_clip.frames) - 1), p)
            prev_upload_id = _save_anchor_upload(db, p, owner_id)
        if next_clip is not None:
            p = anchor_path(episode, shot.shot_no, "next")
            media.extract_frame(next_clip.path, 0, p)
            next_upload_id = _save_anchor_upload(db, p, owner_id)

    characters = (
        db.query(DramaCharacter)
        .filter(DramaCharacter.project_id == episode.project_id)
        .all()
    )
    scene = (
        db.query(DramaScene)
        .filter(DramaScene.episode_id == episode.id, DramaScene.scene_no == shot.scene_no)
        .first()
    )
    base_prompt = composer.compose_segment_prompt(shot, characters, scene, project)
    ref_ids = composer.patch_ref_images(
        prev_upload_id, next_upload_id, shot, characters, scene,
    )
    prompt = composer.patch_prompt(
        base_prompt, reason.strip(), prev_upload_id is not None, next_upload_id is not None,
    )
    window_frames = int(window["frames"])
    seg_plan = comfyui.plan_director_segments(
        [{"prompt": prompt, "duration": window_frames / DRAMA_FPS, "ref_image_ids": ref_ids}],
        aspect_ratio=project.aspect_ratio if project else "16:9",
        fps=DRAMA_FPS, overlap_frames=0,
    )
    generated_frames = seg_plan["segments"][0]["frames"]
    seg_plan["segments"][0]["seed"] = random.randint(0, 2 ** 63 - 1)

    prev_status = _pick_prev_status(db, shot)
    plan = {
        "mode": "patch",
        "fps": DRAMA_FPS,
        "shot_id": shot.id,
        "shot_no": shot.shot_no,
        "window": {
            "shot_id": shot.id, "shot_no": shot.shot_no,
            "start_frame": 0, "end_frame": window_frames, "frames": window_frames,
            "duration": round(window_frames / DRAMA_FPS, 4),
        },
        "generated_frames": generated_frames,
        "prev_status": prev_status,
        "anchor": bool(anchor),
        "prev_upload_id": prev_upload_id,
        "next_upload_id": next_upload_id,
        "segments": seg_plan["segments"],
        "total_frames": seg_plan["total_frames"],
        "width": seg_plan["width"],
        "height": seg_plan["height"],
        "overlap_frames": 0,
    }
    duration_int = max(1, round(window_frames / DRAMA_FPS))
    cost = compute_director_cost(duration_int)
    task = Task(
        user_id=owner_id,
        mode="director",
        prompt=(f"短剧#{episode.id} 第{episode.episode_no}集 返修镜 {shot.shot_no}"[:2000]),
        aspect_ratio=project.aspect_ratio if project else "16:9",
        duration=duration_int,
        resolution="768p",
        enhance=False,
        scene="drama_patch",
        segments=json.dumps(seg_plan["segments"], ensure_ascii=False),
        status="queued",
    )
    db.add(task)
    db.flush()
    run = DramaRun(
        episode_id=episode.id,
        task_id=task.id,
        mode="patch",
        status="queued",
        shot_ids=json.dumps([shot.id]),
        plan=json.dumps(plan, ensure_ascii=False),
        spec_hash=episode.spec_hash,
        cost=0,
    )
    db.add(run)
    db.flush()

    claimed = db.execute(
        update(DramaEpisode)
        .where(DramaEpisode.id == episode.id, DramaEpisode.active_run_id.is_(None))
        .values(active_run_id=run.id, status="rendering", last_error="")
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        db.rollback()
        raise DramaError(409, "本集已有进行中的生成任务，请等待完成")
    db.execute(
        update(DramaShot).where(DramaShot.id == shot.id)
        .values(status="rejected").execution_options(synchronize_session=False)
    )
    db.commit()
    _snapshot(episode, run.id, plan)
    logger.info(
        "短剧集 #%s 返修镜 %s 提交 run #%s：窗口 %d 帧 / 生成 %d 帧 / 预估 %d 积分",
        episode.id, shot.shot_no, run.id, window_frames, generated_frames, cost,
    )
    return {"run_id": run.id, "cost": cost, "window_frames": window_frames}


# ---------------------------------------------------------------- 后处理
def _claim_postprocess(run_id: int) -> bool:
    with SessionLocal() as db:
        rowcount = db.execute(
            update(DramaRun)
            .where(DramaRun.id == run_id, DramaRun.status == "queued")
            .values(status="postprocess", started_at=datetime.now())
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        return bool(rowcount)


def _prepare(run_id: int) -> Optional[dict]:
    with SessionLocal() as db:
        run = db.get(DramaRun, run_id)
        if run is None or run.status != "postprocess":
            return None
        task = db.get(Task, run.task_id)
        episode = db.get(DramaEpisode, run.episode_id)
        if task is None or episode is None:
            return None
        try:
            plan = json.loads(run.plan or "{}")
        except (TypeError, ValueError):
            plan = {}
        shots = (
            db.query(DramaShot)
            .filter(DramaShot.episode_id == episode.id)
            .order_by(DramaShot.shot_no)
            .all()
        )
        clip_map = {}
        for shot in shots:
            clip = current_clip(db, shot.id)
            if clip is not None:
                clip_map[shot.id] = {
                    "id": clip.id, "path": clip.path, "frames": clip.frames,
                    "qa": clip.qa, "spec_hash": clip.spec_hash,
                    "version_no": clip.version_no,
                }
        return {
            "run_id": run.id,
            "mode": run.mode,
            "spec_hash": run.spec_hash,
            "task_id": task.id,
            "task_cost": task.cost,
            "task_path": task.video_path,
            "plan": plan,
            "episode": {
                "id": episode.id, "project_id": episode.project_id,
                "episode_no": episode.episode_no,
            },
            "shots": [{"id": s.id, "shot_no": s.shot_no, "status": s.status} for s in shots],
            "clips": clip_map,
        }


def _render_files(data: dict) -> dict:
    """纯文件阶段：切片 / 拼接 / 质检 / 联系表。不写 DB。"""
    plan = data["plan"]
    version = data["run_id"]
    master = Path(data["task_path"] or "")
    if not master.exists():
        raise media.MediaError("原始产物文件缺失（task.video_path 为空或已被清理）")
    episode = DramaEpisode()
    episode.id = data["episode"]["id"]
    episode.project_id = data["episode"]["project_id"]
    episode.episode_no = data["episode"]["episode_no"]

    shot_by_id = {s["id"]: s for s in data["shots"]}
    clip_files: dict = {}
    qa: dict = {}
    has_audio = media.probe_has_audio(master)

    if data["mode"] == "full":
        windows = plan.get("windows") or []
        if not windows:
            raise media.MediaError("整集排版快照缺少 windows")
        for window in windows:
            shot = shot_by_id.get(window["shot_id"])
            if shot is None:
                raise media.MediaError("镜头与排版快照不一致")
            out = clip_version_path(episode, shot["shot_no"], version)
            media.slice_frames(
                master, out, int(window["start_frame"]), int(window["end_frame"]),
                fps=DRAMA_FPS, has_audio=has_audio,
            )
            clip_files[shot["id"]] = out
        ordered = [clip_files[w["shot_id"]] for w in windows]
        for i in range(len(windows) - 1):
            metric = media.seam_metrics(
                clip_files[windows[i]["shot_id"]], int(windows[i]["frames"]),
                clip_files[windows[i + 1]["shot_id"]],
            )
            left, right = windows[i]["shot_id"], windows[i + 1]["shot_id"]
            qa.setdefault(left, {})["out"] = metric
            qa.setdefault(right, {})["in"] = metric
    else:
        window = plan.get("window") or {}
        shot_id = plan.get("shot_id")
        shot = shot_by_id.get(shot_id)
        if not window or shot is None:
            raise media.MediaError("返修排版快照缺少目标镜窗口")
        frames = int(window["frames"])
        out = clip_version_path(episode, shot["shot_no"], version)
        generated = int(plan.get("generated_frames") or 0)
        if generated >= frames:
            media.slice_frames(master, out, 0, frames, fps=DRAMA_FPS, has_audio=has_audio)
        else:
            media.pad_frames(master, out, frames, fps=DRAMA_FPS)
        clip_files[shot_id] = out
        # 目标镜两侧接缝
        shots = data["shots"]
        pos = next((i for i, s in enumerate(shots) if s["id"] == shot_id), None)
        prev_shot = shots[pos - 1] if pos is not None and pos > 0 else None
        next_shot = shots[pos + 1] if pos is not None and pos + 1 < len(shots) else None
        if prev_shot and prev_shot["id"] in data["clips"]:
            prev_clip = data["clips"][prev_shot["id"]]
            metric = media.seam_metrics(prev_clip["path"], int(prev_clip["frames"]), out)
            qa.setdefault(prev_shot["id"], {})["out"] = metric
            qa.setdefault(shot_id, {})["in"] = metric
        if next_shot and next_shot["id"] in data["clips"]:
            nxt = data["clips"][next_shot["id"]]
            metric = media.seam_metrics(out, frames, nxt["path"])
            qa.setdefault(shot_id, {})["out"] = metric
            qa.setdefault(next_shot["id"], {})["in"] = metric
        ordered = []
        for item in shots:
            if item["id"] == shot_id:
                ordered.append(out)
            elif item["id"] in data["clips"]:
                ordered.append(Path(data["clips"][item["id"]]["path"]))
            else:
                raise media.MediaError(f"镜头 {item['shot_no']} 缺少当前成片，无法重拼")

    final_path = final_version_path(episode, version)
    media.concat_clips(ordered, final_path, fps=DRAMA_FPS)
    if data["mode"] == "full":
        expected_frames = sum(int(w["frames"]) for w in plan.get("windows") or [])
    else:
        expected_frames = int((plan.get("window") or {}).get("frames") or 0)
    final_duration = media.probe_duration(final_path) or round(expected_frames / DRAMA_FPS, 3)
    sheet = media.make_sheet(ordered, sheet_path(episode, version))
    return {
        "version": version,
        "clip_files": clip_files,
        "qa": qa,
        "final_path": final_path,
        "final_duration": final_duration,
        "sheet_path": sheet,
    }


def _commit(data: dict, result: dict) -> None:
    with SessionLocal() as db:
        run = db.get(DramaRun, data["run_id"])
        if run is None or run.status != "postprocess":
            return
        episode = db.get(DramaEpisode, data["episode"]["id"])
        if episode is None:
            return
        version = result["version"]
        now = datetime.now()

        # 1) clip 指针切换
        for shot_id, path in result["clip_files"].items():
            db.execute(
                update(DramaArtifact)
                .where(DramaArtifact.shot_id == shot_id, DramaArtifact.kind == "clip",
                       DramaArtifact.is_current.is_(True))
                .values(is_current=False).execution_options(synchronize_session=False)
            )
            if data["mode"] == "full":
                frames = next(
                    (int(w["frames"]) for w in data["plan"].get("windows", [])
                     if w["shot_id"] == shot_id), 0,
                )
            else:
                frames = int((data["plan"].get("window") or {}).get("frames") or 0)
            shot = db.get(DramaShot, shot_id)
            qa_payload = result["qa"].get(shot_id)
            db.add(DramaArtifact(
                episode_id=episode.id, shot_id=shot_id, kind="clip", version_no=version,
                path=str(path), duration=round(frames / DRAMA_FPS, 4), frames=frames,
                spec_hash=data["spec_hash"], task_id=data["task_id"],
                qa=json.dumps(qa_payload, ensure_ascii=False) if qa_payload else "",
                is_current=True,
            ))
            if shot is not None:
                shot.version_no = version
                shot.spec_hash = data["spec_hash"]
                if data["mode"] == "full":
                    shot.status = "rendered"
                    if qa_payload:
                        shot.qa = json.dumps(qa_payload, ensure_ascii=False)
                else:
                    shot.status = "rendered"
                    shot.repair_count = (shot.repair_count or 0) + 1
                    shot.repair_cost_total = (shot.repair_cost_total or 0) + int(data["task_cost"] or 0)
                    if qa_payload:
                        shot.qa = json.dumps(qa_payload, ensure_ascii=False)

        # 2) 相邻镜接缝 qa（返修补丁）
        if data["mode"] == "patch":
            for shot_id, payload in result["qa"].items():
                if shot_id == data["plan"].get("shot_id"):
                    continue
                shot = db.get(DramaShot, shot_id)
                if shot is None:
                    continue
                try:
                    existing = json.loads(shot.qa or "{}")
                except (TypeError, ValueError):
                    existing = {}
                existing.update({k: v for k, v in payload.items() if v})
                shot.qa = json.dumps(existing, ensure_ascii=False)

        # 3) master
        db.execute(
            update(DramaArtifact)
            .where(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "master")
            .values(is_current=False).execution_options(synchronize_session=False)
        )
        db.add(DramaArtifact(
            episode_id=episode.id, shot_id=None, kind="master", version_no=version,
            path=data["task_path"] or "", spec_hash=data["spec_hash"],
            task_id=data["task_id"], is_current=True,
        ))

        # 4) final
        db.execute(
            update(DramaArtifact)
            .where(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "final")
            .values(is_current=False).execution_options(synchronize_session=False)
        )
        final = DramaArtifact(
            episode_id=episode.id, shot_id=None, kind="final", version_no=version,
            path=str(result["final_path"]), duration=result["final_duration"],
            spec_hash=data["spec_hash"], task_id=data["task_id"], is_current=True,
        )
        db.add(final)
        db.flush()
        episode.final_artifact_id = final.id
        episode.final_duration = result["final_duration"]
        episode.spec_hash = data["spec_hash"]
        episode.status = "review"
        episode.last_error = ""
        if episode.active_run_id == run.id:
            episode.active_run_id = None

        # 5) sheet
        if result.get("sheet_path"):
            db.execute(
                update(DramaArtifact)
                .where(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "sheet")
                .values(is_current=False).execution_options(synchronize_session=False)
            )
            db.add(DramaArtifact(
                episode_id=episode.id, shot_id=None, kind="sheet", version_no=version,
                path=str(result["sheet_path"]), spec_hash=data["spec_hash"],
                task_id=data["task_id"], is_current=True,
            ))

        # 6) run 终态
        run.status = "done"
        run.cost = int(data["task_cost"] or 0)
        run.error = ""
        run.finished_at = now
        db.commit()
        logger.info(
            "短剧 run #%s 后处理完成：%d clip / 成片 %.2fs",
            run.id, len(result["clip_files"]), result["final_duration"],
        )
    with SessionLocal() as db:
        episode = db.get(DramaEpisode, data["episode"]["id"])
        if episode is not None:
            rebuild_stable_paths(db, episode)


def postprocess(run_id: int) -> None:
    """后处理唯一入口（幂等）：先抢占，再文件阶段，最后原子切指针。"""
    if not _claim_postprocess(run_id):
        return
    try:
        data = _prepare(run_id)
        if data is None:
            return
        result = _render_files(data)
        _commit(data, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("短剧 run #%s 后处理失败", run_id)
        fail_run(run_id, f"后处理失败：{str(exc)[:400]}")


def fail_run(run_id: int, error: str) -> None:
    """失败回写：run=failed、清 active_run_id、集回退、patch 恢复 prev_status。"""
    with SessionLocal() as db:
        run = db.get(DramaRun, run_id)
        if run is None or run.status in ("done", "cancelled"):
            return
        episode = db.get(DramaEpisode, run.episode_id)
        run.status = "failed"
        run.error = (error or "生成失败")[:1000]
        run.finished_at = datetime.now()
        if episode is not None:
            if run.mode == "patch":
                try:
                    plan = json.loads(run.plan or "{}")
                except (TypeError, ValueError):
                    plan = {}
                shot_id = plan.get("shot_id")
                shot = db.get(DramaShot, shot_id) if shot_id else None
                if shot is not None:
                    shot.status = plan.get("prev_status") or "rendered"
            final = current_final(db, episode)
            episode.status = "review" if (final and Path(final.path).exists()) else "shots_ready"
            episode.last_error = run.error
            episode.active_run_id = None
        db.commit()
        logger.error("短剧 run #%s 失败：%s", run_id, run.error)


# ---------------------------------------------------------------- worker 回调
def on_task_finished(task_id: int) -> None:
    """worker 在 director 任务进入终态时调用（同步，会话已关闭）。"""
    with SessionLocal() as db:
        run = db.query(DramaRun).filter(DramaRun.task_id == task_id).first()
        if run is None:
            return
        task = db.get(Task, task_id)
        if task is None:
            return
        run_id, status, error = run.id, task.status, task.error
    if status == "done":
        postprocess(run_id)
    elif status == "failed":
        fail_run(run_id, error)


# ---------------------------------------------------------------- 稳定路径
def rebuild_stable_paths(db: Session, episode: DramaEpisode) -> None:
    """按 DB current 指针重建稳定路径；派生数据，失败不影响 DB。"""
    shots = db.query(DramaShot).filter(DramaShot.episode_id == episode.id).all()
    for shot in shots:
        clip = current_clip(db, shot.id)
        if clip is None:
            continue
        src = Path(clip.path)
        if not src.exists():
            continue
        try:
            _atomic_copy(src, clip_stable_path(episode, shot.shot_no))
        except media.MediaError:
            logger.warning("集 #%s 镜 %s 稳定路径重建失败", episode.id, shot.shot_no)
    final = current_final(db, episode)
    if final is not None and Path(final.path).exists():
        try:
            _atomic_copy(Path(final.path), final_stable_path(episode))
        except media.MediaError:
            logger.warning("集 #%s 成片稳定路径重建失败", episode.id)


# ---------------------------------------------------------------- 巡扫
def sweep_once() -> None:
    """30s 巡扫：推进 queued、重置超时 postprocess、清空终态指针、补稳定路径。"""
    to_process: list = []
    to_fail: list = []
    with SessionLocal() as db:
        now = datetime.now()
        for run in db.query(DramaRun).filter(DramaRun.status == "queued").all():
            task = db.get(Task, run.task_id)
            if task is None:
                continue
            if task.status == "done":
                to_process.append(run.id)
            elif task.status == "failed":
                to_fail.append((run.id, task.error))
        for run in db.query(DramaRun).filter(DramaRun.status == "postprocess").all():
            started = run.started_at or run.created_at
            if started and now - started > timedelta(seconds=POSTPROCESS_TIMEOUT_SECONDS):
                logger.warning("短剧 run #%s 后处理超时，重置为 queued 重跑", run.id)
                run.status = "queued"
                run.started_at = None
                to_process.append(run.id)
        for episode in db.query(DramaEpisode).filter(DramaEpisode.active_run_id.isnot(None)).all():
            run = db.get(DramaRun, episode.active_run_id)
            if run is None or run.status in ("done", "failed", "cancelled"):
                episode.active_run_id = None
        db.commit()

    for run_id, error in to_fail:
        fail_run(run_id, error)
    for run_id in to_process:
        postprocess(run_id)

    # 稳定路径兜底：只补缺失，不做全量重建
    with SessionLocal() as db:
        episodes = (
            db.query(DramaEpisode)
            .filter(DramaEpisode.final_artifact_id.isnot(None), DramaEpisode.active_run_id.is_(None))
            .limit(50)
            .all()
        )
        for episode in episodes:
            if not final_stable_path(episode).exists():
                rebuild_stable_paths(db, episode)


async def sweep_loop() -> None:
    import asyncio

    while True:
        try:
            await asyncio.to_thread(sweep_once)
        except Exception:  # noqa: BLE001
            logger.exception("短剧巡扫异常")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


# ---------------------------------------------------------------- 审片动作
def repair_run(run_id: int) -> None:
    """0 成本重跑后处理（task 已成功、run 后处理失败）。"""
    with SessionLocal() as db:
        run = db.get(DramaRun, run_id)
        if run is None:
            raise DramaError(404, "任务不存在")
        task = db.get(Task, run.task_id)
        if task is None or task.status != "done" or not task.video_path:
            raise DramaError(409, "原始产物尚未生成完成，无法修复后处理")
        if run.status in ("queued", "postprocess"):
            raise DramaError(409, "后处理正在进行中")
        run.status = "queued"
        run.error = ""
        run.started_at = None
        db.commit()
    postprocess(run_id)


def cancel_run(db: Session, run: DramaRun) -> None:
    """只允许取消「queued 且从未被 worker 领取」的任务。"""
    task = db.get(Task, run.task_id)
    if run.status != "queued" or task is None:
        raise DramaError(409, "任务已开始执行，导演台独占整卡，生成中不可取消")
    if task.worker_url or task.started_at is not None or task.status != "queued":
        raise DramaError(409, "任务已开始执行，导演台独占整卡，生成中不可取消")
    claimed = db.execute(
        update(Task)
        .where(Task.id == task.id, Task.status == "queued",
               Task.worker_url == "", Task.started_at.is_(None))
        .values(status="cancelled", finished_at=datetime.now())
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        db.rollback()
        raise DramaError(409, "任务刚好被 worker 领取，无法取消")
    run.status = "cancelled"
    run.finished_at = datetime.now()
    episode = db.get(DramaEpisode, run.episode_id)
    if episode is not None:
        final = current_final(db, episode)
        episode.status = "review" if (final and Path(final.path).exists()) else "shots_ready"
        episode.active_run_id = None
    db.commit()


def finish_episode(db: Session, episode: DramaEpisode) -> None:
    """交付门 G4：final 非 stale、无 rejected、每镜有 current clip。"""
    if episode.status != "review":
        raise DramaError(409, "当前集不在审片阶段，无法交付")
    if episode.active_run_id:
        raise DramaError(409, f"本集正在生成中（run #{episode.active_run_id}），请等待完成")
    final = current_final(db, episode)
    if final is None or not Path(final.path).exists():
        raise DramaError(409, "成片不存在，请先生成整集")
    if final.spec_hash != episode.spec_hash:
        raise DramaError(409, "成片已过旧（分镜或角色卡已改动），无法交付；请重新生成或重新编辑")
    shots = (
        db.query(DramaShot).filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no).all()
    )
    for shot in shots:
        clip = current_clip(db, shot.id)
        if clip is None:
            raise DramaError(409, f"第 {shot.shot_no} 镜缺少成片，无法交付")
        if clip.spec_hash != episode.spec_hash:
            raise DramaError(409, f"第 {shot.shot_no} 镜是旧版（分镜已改动），请重新生成整集")
        if shot.status == "rejected":
            raise DramaError(409, f"第 {shot.shot_no} 镜仍在返修中，无法交付")
    for shot in shots:
        if shot.status == "rendered":
            shot.status = "approved"
    claimed = db.execute(
        update(DramaEpisode)
        .where(DramaEpisode.id == episode.id, DramaEpisode.status == "review",
               DramaEpisode.active_run_id.is_(None))
        .values(status="done")
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        db.rollback()
        raise DramaError(409, "集状态已变化，请刷新后重试")
    db.commit()


def reopen_episode(db: Session, episode: DramaEpisode) -> None:
    """已交付集回到分镜；不改 spec_hash、不扣费（条件更新防与 render 抢占交错）。"""
    claimed = db.execute(
        update(DramaEpisode)
        .where(DramaEpisode.id == episode.id,
               DramaEpisode.status.in_(("review", "done")),
               DramaEpisode.active_run_id.is_(None))
        .values(status="shots_ready")
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        db.rollback()
        raise DramaError(409, "当前集无法重新编辑（可能正在生成）")
    db.commit()


# ---------------------------------------------------------------- 回滚
def rollback(db: Session, episode: DramaEpisode, artifact_id: int) -> Optional[DramaShot]:
    if episode.active_run_id:
        raise DramaError(409, f"本集正在生成中（run #{episode.active_run_id}），请等待完成")
    if episode.status == "rendering":
        raise DramaError(409, "本集正在生成中，请等待完成")
    if episode.status not in ("review", "done"):
        raise DramaError(409, "当前集不在审片或交付阶段，无法回滚")
    artifact = db.get(DramaArtifact, artifact_id)
    if artifact is None or artifact.episode_id != episode.id:
        raise DramaError(404, "版本不存在")
    if artifact.kind not in ("clip", "final"):
        raise DramaError(400, "只支持回滚镜头或成片版本")

    shot = None
    if artifact.kind == "clip":
        if not artifact.shot_id:
            raise DramaError(400, "镜头版本缺少归属")
        db.execute(
            update(DramaArtifact)
            .where(DramaArtifact.shot_id == artifact.shot_id, DramaArtifact.kind == "clip",
                   DramaArtifact.is_current.is_(True))
            .values(is_current=False).execution_options(synchronize_session=False)
        )
        artifact.is_current = True
        shot = db.get(DramaShot, artifact.shot_id)
        if shot is not None:
            shot.version_no = artifact.version_no
            shot.status = "rendered"
        # 0 成本重拼 final
        shots = (
            db.query(DramaShot).filter(DramaShot.episode_id == episode.id)
            .order_by(DramaShot.shot_no).all()
        )
        paths = []
        for item in shots:
            clip = current_clip(db, item.id)
            if clip is None or not Path(clip.path).exists():
                db.rollback()
                raise DramaError(409, f"第 {item.shot_no} 镜缺少成片，无法重拼成片")
            paths.append(Path(clip.path))
        version = artifact.version_no or 0
        target = final_version_path(episode, version)
        try:
            media.concat_clips(paths, target, fps=DRAMA_FPS)
        except media.MediaError as exc:
            db.rollback()
            raise DramaError(409, f"重新拼接成片失败：{exc}") from exc
        db.execute(
            update(DramaArtifact)
            .where(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "final")
            .values(is_current=False).execution_options(synchronize_session=False)
        )
        final = DramaArtifact(
            episode_id=episode.id, shot_id=None, kind="final", version_no=version,
            path=str(target), duration=media.probe_duration(target),
            spec_hash=episode.spec_hash, is_current=True,
        )
        db.add(final)
        db.flush()
        episode.final_artifact_id = final.id
        episode.final_duration = final.duration
    else:
        db.execute(
            update(DramaArtifact)
            .where(DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "final",
                   DramaArtifact.is_current.is_(True))
            .values(is_current=False).execution_options(synchronize_session=False)
        )
        artifact.is_current = True
        episode.final_artifact_id = artifact.id
        episode.final_duration = artifact.duration

    if episode.status == "done":
        episode.status = "review"
    db.commit()
    rebuild_stable_paths(db, episode)
    logger.info("短剧集 #%s 回滚到 artifact #%s", episode.id, artifact_id)
    return shot

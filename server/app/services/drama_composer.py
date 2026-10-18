"""短剧编排器：提示词组装、39 帧补偿排版、参考图预算、spec_hash、就绪检查。

帧补偿事实（插件 TIMELINE_SEGMENT_WINDOWS_CN）：
- 第 i 段从 cursor - overlap 开始，前 39 帧是对上一段尾部的引导回放；
- 第 1 镜按 duration 提交；第 2 镜起必须提交 duration + 39/24 秒，否则每镜损失 39 帧；
- 可见窗口 start = seg.start_frame + (39 if i > 0 else 0)，end = seg.end_frame（开区间）。
"""
import hashlib
import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from ..config import settings
from ..models import DramaCharacter, DramaEpisode, DramaProject, DramaScene, DramaShot, Upload, User
from . import comfyui
from .billing import compute_director_cost

logger = logging.getLogger(__name__)

DRAMA_FPS = 24
DRAMA_OVERLAP = comfyui.DIRECTOR_OVERLAP_FRAMES
MAX_SEG_IMAGES = 9
_ROLE_ORDER = {"main": 0, "support": 1, "extra": 2}


# ---------------------------------------------------------------- JSON 辅助
def load_json(raw, default):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- 提示词
def _block(label: str, text: str) -> str:
    text = (text or "").strip()
    return f"[{label}] {text}" if text else ""


def compose_segment_prompt(shot: DramaShot, characters: list, scene: Optional[DramaScene],
                           project: DramaProject) -> str:
    """按固定顺序拼提示词；缺省块整行省略，不留空标签。"""
    char_lines = []
    ordered = sorted(
        [c for c in characters if c.id in (load_json(shot.character_ids, []) or [])],
        key=lambda c: (_ROLE_ORDER.get(c.role, 9), c.id),
    )
    for char in ordered[:4]:
        bits = [b for b in (char.appearance, f"服装：{char.costume}" if char.costume else "") if b]
        char_lines.append(f"{char.name}（{'；'.join(bits)}）" if bits else char.name)
    scene_line = ""
    if scene is not None:
        parts = [scene.name]
        if scene.time_of_day:
            parts.append(scene.time_of_day)
        if scene.description:
            parts.append(scene.description)
        scene_line = "｜".join(p for p in parts if p)
    camera_parts = [p for p in (shot.shot_size, shot.camera, shot.movement) if p]
    blocks = [
        _block("风格", project.style_bible if project else ""),
        _block("场景", scene_line),
        _block("角色", "；".join(char_lines)),
        _block("镜头", "，".join(camera_parts)),
        _block("动作", shot.prompt),
        _block("台词", f"{shot.dialogue}（口型同步，保留原声）" if shot.dialogue else ""),
        _block("声音", shot.audio_note),
        _block("连贯", shot.continuity_note),
        _block("禁止", "画面内不出现字幕、水印、文字叠加、台标；不出现额外人物或肢体。"),
    ]
    return "\n".join(b for b in blocks if b)[:4000]


def patch_prompt(base_prompt: str, reason: str, has_prev: bool, has_next: bool) -> str:
    """返修提示词：首尾图锚 + 单镜连续 + 用户意见。"""
    lines = []
    picture = 1
    if has_prev:
        lines.append(f"Start exactly on the framing, subject position and lighting of <Picture {picture}>.")
        picture += 1
    if has_next:
        lines.append(f"End exactly on the framing, subject position and lighting of <Picture {picture}>.")
    lines.append("Single continuous take; no cut, no lens change, no new subjects.")
    return "\n".join(lines) + "\n\n" + base_prompt + f"\n[返修要求] {reason}"


# ---------------------------------------------------------------- 参考图预算
def _append_upload(ids: list, seen: set, upload_id, warnings: list, label: str) -> None:
    if not upload_id or upload_id in seen:
        return
    if len(ids) >= MAX_SEG_IMAGES:
        warnings.append(f"{label}超出每段 9 张上限，已截断")
        return
    seen.add(upload_id)
    ids.append(int(upload_id))


def collect_ref_images(shot: DramaShot, characters: list, scene: Optional[DramaScene],
                       warnings: Optional[list] = None) -> list:
    """整集生成参考图：main 卡 → 场景锚 → 用户参考 → support 卡。"""
    warnings = warnings if warnings is not None else []
    ids: list = []
    seen: set = set()
    selected = [c for c in characters if c.id in (load_json(shot.character_ids, []) or [])]
    main = [c for c in selected if c.role == "main"]
    support = [c for c in selected if c.role != "main"]
    for char in main[:3]:
        _append_upload(ids, seen, char.card_upload_id, warnings, f"角色「{char.name}」定妆图")
    if scene is not None:
        _append_upload(ids, seen, scene.anchor_upload_id, warnings, "场景锚图")
    for upload_id in load_json(shot.ref_upload_ids, []) or []:
        _append_upload(ids, seen, upload_id, warnings, "用户参考图")
    for char in support:
        _append_upload(ids, seen, char.card_upload_id, warnings, f"角色「{char.name}」定妆图")
    return ids


def patch_ref_images(prev_upload: Optional[int], next_upload: Optional[int], shot: DramaShot,
                     characters: list, scene: Optional[DramaScene]) -> list:
    """返修参考图：上一镜末帧 → 下一镜首帧 → main 卡 → 场景锚 → 用户参考。"""
    ids: list = []
    seen: set = set()
    warnings: list = []
    _append_upload(ids, seen, prev_upload, warnings, "上一镜末帧")
    _append_upload(ids, seen, next_upload, warnings, "下一镜首帧")
    selected = [c for c in characters if c.id in (load_json(shot.character_ids, []) or [])]
    for char in [c for c in selected if c.role == "main"][:3]:
        _append_upload(ids, seen, char.card_upload_id, warnings, f"角色「{char.name}」定妆图")
    if scene is not None:
        _append_upload(ids, seen, scene.anchor_upload_id, warnings, "场景锚图")
    for upload_id in load_json(shot.ref_upload_ids, []) or []:
        _append_upload(ids, seen, upload_id, warnings, "用户参考图")
    return ids


# ---------------------------------------------------------------- 排版
def build_plan(db: Session, episode: DramaEpisode) -> dict:
    """把镜头表排版为导演台段清单 + 可见窗口；异常统一抛 ValueError。"""
    project = db.get(DramaProject, episode.project_id)
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no)
        .all()
    )
    if not shots:
        raise ValueError("请先规划分镜")
    if len(shots) > settings.drama_max_shots:
        raise ValueError(f"镜头数 {len(shots)} 超过上限 {settings.drama_max_shots}")
    characters = (
        db.query(DramaCharacter)
        .filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id)
        .all()
    )
    scenes = db.query(DramaScene).filter(DramaScene.episode_id == episode.id).all()
    scene_by_no = {s.scene_no: s for s in scenes}

    warnings: list = []
    segments = []
    for i, shot in enumerate(shots):
        if not (settings.drama_min_shot_seconds <= shot.duration_s <= settings.drama_max_shot_seconds):
            raise ValueError(
                f"第 {shot.shot_no} 镜时长需在 {settings.drama_min_shot_seconds:g}–"
                f"{settings.drama_max_shot_seconds:g} 秒（当前 {shot.duration_s:g} 秒）"
            )
        prompt = compose_segment_prompt(shot, characters, scene_by_no.get(shot.scene_no), project)
        refs = collect_ref_images(shot, characters, scene_by_no.get(shot.scene_no), warnings)
        duration = float(shot.duration_s) + (DRAMA_OVERLAP / DRAMA_FPS if i > 0 else 0.0)
        segments.append({
            "shot_id": shot.id,
            "shot_no": shot.shot_no,
            "prompt": prompt,
            "duration": duration,
            "ref_image_ids": refs,
            "ref_count": len(refs),
        })

    placed = comfyui.plan_director_segments(
        segments, aspect_ratio=project.aspect_ratio if project else "16:9",
        fps=DRAMA_FPS, overlap_frames=DRAMA_OVERLAP,
    )
    windows = []
    for i, seg in enumerate(placed["segments"]):
        source = segments[i]
        start = seg["start_frame"] + (placed["overlap_frames"] if i > 0 else 0)
        end = seg["end_frame"]
        frames = end - start
        if frames <= 0:
            raise ValueError(f"第 {source['shot_no']} 镜可见窗口为 0 帧，请调长时长")
        seg.update({
            "shot_id": source["shot_id"],
            "shot_no": source["shot_no"],
            "visible_start_frame": start,
            "visible_end_frame": end,
            "visible_frames": frames,
            "ref_count": source["ref_count"],
        })
        windows.append({
            "shot_id": source["shot_id"],
            "shot_no": source["shot_no"],
            "start_frame": start,
            "end_frame": end,
            "frames": frames,
            "duration": round(frames / DRAMA_FPS, 4),
        })
    total_visible = sum(w["frames"] for w in windows)
    if total_visible != placed["total_frames"]:
        raise ValueError(
            f"窗口合计 {total_visible} 帧与总帧数 {placed['total_frames']} 不一致（内部错误）"
        )
    return {
        "fps": placed["fps"],
        "overlap_frames": placed["overlap_frames"],
        "width": placed["width"],
        "height": placed["height"],
        "total_frames": placed["total_frames"],
        "segments": placed["segments"],
        "windows": windows,
        "mode": "full",
        "warnings": warnings,
    }


def estimate_cost(plan: dict) -> int:
    """与 worker 完全一致的预估计费口径。"""
    duration_int = max(1, round(plan["total_frames"] / DRAMA_FPS))
    return compute_director_cost(duration_int)


# ---------------------------------------------------------------- 指纹
def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _upload_md5(db: Session, upload_id: Optional[int], cache: dict) -> str:
    if not upload_id:
        return ""
    if upload_id in cache:
        return cache[upload_id]
    upload = db.get(Upload, upload_id)
    cache[upload_id] = upload.md5 if upload else ""
    return cache[upload_id]


def compute_spec_hash(db: Session, episode: DramaEpisode) -> str:
    """影响最终画面的字段才进哈希；locked/status 等流程字段不入。"""
    project = db.get(DramaProject, episode.project_id)
    characters = (
        db.query(DramaCharacter)
        .filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id)
        .all()
    )
    scenes = (
        db.query(DramaScene)
        .filter(DramaScene.episode_id == episode.id)
        .order_by(DramaScene.scene_no)
        .all()
    )
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no)
        .all()
    )
    cache: dict = {}
    payload = {
        "style_bible": project.style_bible if project else "",
        "aspect_ratio": project.aspect_ratio if project else "16:9",
        "fps": DRAMA_FPS,
        "overlap": DRAMA_OVERLAP,
        "characters": [{
            "id": c.id, "name": c.name, "role": c.role,
            "appearance": c.appearance, "personality": c.personality,
            "costume": c.costume, "voice": c.voice,
            "card_upload_id": c.card_upload_id,
            "card_md5": _upload_md5(db, c.card_upload_id, cache),
        } for c in characters],
        "scenes": [{
            "scene_no": s.scene_no, "name": s.name, "location": s.location,
            "time_of_day": s.time_of_day, "description": s.description,
            "anchor_upload_id": s.anchor_upload_id,
            "anchor_md5": _upload_md5(db, s.anchor_upload_id, cache),
        } for s in scenes],
        "shots": [{
            "shot_no": s.shot_no, "scene_no": s.scene_no, "beat_id": s.beat_id,
            "prompt": s.prompt, "duration_s": s.duration_s,
            "shot_size": s.shot_size, "camera": s.camera, "movement": s.movement,
            "character_ids": sorted(load_json(s.character_ids, []) or []),
            "refs": [{
                "id": uid, "md5": _upload_md5(db, uid, cache),
            } for uid in sorted(load_json(s.ref_upload_ids, []) or [])],
            "dialogue": s.dialogue, "audio_note": s.audio_note,
            "continuity_note": s.continuity_note,
        } for s in shots],
    }
    return hashlib.sha1(_canonical(payload).encode("utf-8")).hexdigest()


def refresh_episode_spec(db: Session, episode: DramaEpisode) -> str:
    episode.spec_hash = compute_spec_hash(db, episode)
    for shot in db.query(DramaShot).filter(DramaShot.episode_id == episode.id).all():
        shot.spec_hash = episode.spec_hash
    return episode.spec_hash


def refresh_project_specs(db: Session, project_id: int) -> None:
    for episode in db.query(DramaEpisode).filter(DramaEpisode.project_id == project_id).all():
        refresh_episode_spec(db, episode)


# ---------------------------------------------------------------- 就绪检查
def _item(code: str, message: str, shot_no: Optional[int] = None) -> dict:
    item = {"code": code, "message": message}
    if shot_no is not None:
        item["shot_no"] = shot_no
    return item


def validate_episode(db: Session, episode: DramaEpisode, user: Optional[User] = None) -> dict:
    """G3 的唯一实现：issues 非空即 ready=false；warnings 不阻塞。"""
    project = db.get(DramaProject, episode.project_id)
    characters = (
        db.query(DramaCharacter)
        .filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id)
        .all()
    )
    scenes = db.query(DramaScene).filter(DramaScene.episode_id == episode.id).all()
    shots = (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no)
        .all()
    )
    issues: list = []
    warnings: list = []
    beats = load_json(episode.beats, [])

    if not beats:
        issues.append(_item("SCRIPT_EMPTY", "请先生成剧本"))
    if not shots:
        issues.append(_item("SHOTS_EMPTY", "请先规划分镜"))

    main_chars = [c for c in characters if c.role == "main"]
    for char in main_chars:
        if not char.card_upload_id or not char.locked:
            issues.append(_item("CAST_UNLOCKED", f"「{char.name}」未锁定定妆图"))

    expected = list(range(1, len(shots) + 1))
    if [s.shot_no for s in shots] != expected:
        issues.append(_item("SHOT_NO_GAP", "镜号必须从 1 开始连续"))

    char_ids = {c.id for c in characters}
    scene_nos = {s.scene_no for s in scenes}
    for shot in shots:
        if not (settings.drama_min_shot_seconds <= shot.duration_s <= settings.drama_max_shot_seconds):
            issues.append(_item(
                "SHOT_DURATION",
                f"第 {shot.shot_no} 镜时长需在 {settings.drama_min_shot_seconds:g}–"
                f"{settings.drama_max_shot_seconds:g} 秒",
                shot.shot_no,
            ))
        if len((shot.prompt or "").strip()) < 6:
            issues.append(_item("SHOT_PROMPT_EMPTY", f"第 {shot.shot_no} 镜提示词过短", shot.shot_no))
        if shot.scene_no not in scene_nos:
            issues.append(_item("SHOT_SCENE_MISSING", f"第 {shot.shot_no} 镜引用了不存在的场景", shot.shot_no))
        missing = [cid for cid in (load_json(shot.character_ids, []) or []) if cid not in char_ids]
        if missing:
            issues.append(_item("SHOT_CHARACTER_MISSING", f"第 {shot.shot_no} 镜引用了不存在的角色", shot.shot_no))
        if len(load_json(shot.ref_upload_ids, []) or []) > MAX_SEG_IMAGES:
            warnings.append(_item(
                "SHOT_REF_LIMIT", f"第 {shot.shot_no} 镜参考图超过 9 张，将按优先级截断", shot.shot_no,
            ))

    plan = None
    est_cost = 0
    total_seconds = round(sum(s.duration_s for s in shots), 2)
    if shots:
        try:
            plan = build_plan(db, episode)
            total_seconds = round(plan["total_frames"] / DRAMA_FPS, 2)
            est_cost = estimate_cost(plan)
            warnings.extend(_item("SHOT_REF_LIMIT", w) for w in plan.get("warnings", []))
        except (ValueError, AssertionError, KeyError) as exc:
            issues.append(_item("PLAN_FAILED", f"分镜排版失败：{exc}"))

    if plan and total_seconds > settings.drama_max_episode_seconds:
        issues.append(_item(
            "TOTAL_SECONDS", f"本集 {total_seconds:g} 秒，超过上限 {settings.drama_max_episode_seconds} 秒",
        ))

    target = sum(float(b.get("target_seconds") or 0) for b in beats)
    if plan and target > 0 and abs(total_seconds - target) / target > 0.2:
        drift = round((total_seconds - target) / target * 100)
        warnings.append(_item("DURATION_DRIFT", f"总时长与目标偏差 {drift:+d}%"))

    actor = user or db.get(User, db.get(DramaProject, episode.project_id).user_id)
    balance = actor.credits if actor else 0
    if plan and balance < est_cost:
        warnings.append(_item("INSUFFICIENT_CREDITS", f"余额不足，生成前需充值（差 {est_cost - balance}）"))

    return {
        "ready": not issues,
        "shots": len(shots),
        "total_seconds": total_seconds,
        "est_cost": est_cost,
        "balance": balance,
        "sufficient": balance >= est_cost,
        "issues": issues,
        "warnings": warnings,
        "plan": None if plan is None else {
            "total_frames": plan["total_frames"],
            "fps": plan["fps"],
            "segments": [{
                "shot_no": seg["shot_no"],
                "frames": seg["frames"],
                "visible_frames": seg["visible_frames"],
                "ref_count": seg["ref_count"],
                "prompt": seg["prompt"],
            } for seg in plan["segments"]],
            "windows": plan["windows"],
        },
    }

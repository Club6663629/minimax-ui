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
from ..models import (
    DramaArtifact, DramaCharacter, DramaEpisode, DramaProject, DramaScene, DramaSegment, DramaShot,
    Upload, User,
)
from . import comfyui
from .billing import compute_director_cost

logger = logging.getLogger(__name__)

DRAMA_FPS = 24
DRAMA_OVERLAP = comfyui.DIRECTOR_OVERLAP_FRAMES
MAX_SEG_IMAGES = 9
MIN_SEGMENT_SECONDS = 30.0
MAX_SEGMENTS = 60
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


# ---------------------------------------------------------------- 分段

def episode_shots(db: Session, episode: DramaEpisode) -> list:
    return (
        db.query(DramaShot)
        .filter(DramaShot.episode_id == episode.id)
        .order_by(DramaShot.shot_no)
        .all()
    )


def segment_shots(db: Session, segment: DramaSegment) -> list:
    return (
        db.query(DramaShot)
        .filter(DramaShot.segment_id == segment.id)
        .order_by(DramaShot.shot_no)
        .all()
    )


def episode_segments(db: Session, episode: DramaEpisode) -> list:
    return (
        db.query(DramaSegment)
        .filter(DramaSegment.episode_id == episode.id)
        .order_by(DramaSegment.segment_no)
        .all()
    )


def _split_shots(shots: list, target: float, max_seconds: float, max_shots: int) -> list:
    """按场优先、时长/镜数封顶切段；只切在镜头边界。"""
    groups: list = []
    current: list = []
    for shot in shots:
        if current:
            duration = sum(s.duration_s for s in current)
            cross_scene = shot.scene_no != current[-1].scene_no
            too_long = duration + shot.duration_s > max_seconds
            too_many = len(current) >= max_shots
            reached = duration >= target and duration + shot.duration_s > target * 1.2
            beat_changed = bool(
                shot.beat_id and current[-1].beat_id and shot.beat_id != current[-1].beat_id
            )
            if cross_scene or too_long or too_many or (reached and beat_changed):
                groups.append(current)
                current = []
        current.append(shot)
    if current:
        groups.append(current)

    # 过短且同场的相邻段合并，仍不超过硬上限
    merged: list = []
    for group in groups:
        if merged:
            prev = merged[-1]
            prev_seconds = sum(s.duration_s for s in prev)
            total = prev_seconds + sum(s.duration_s for s in group)
            if (prev_seconds < MIN_SEGMENT_SECONDS
                    and prev[-1].scene_no == group[0].scene_no
                    and total <= max_seconds and len(prev) + len(group) <= max_shots):
                merged[-1] = prev + group
                continue
        merged.append(group)
    return merged


def plan_segments(db: Session, episode: DramaEpisode, target_seconds: Optional[float] = None,
                  force: bool = False) -> list:
    """规则拆段：场优先，30–120s / ≤12 镜；返回按 segment_no 排序的段。"""
    shots = episode_shots(db, episode)
    if not shots:
        raise ValueError("请先规划分镜")
    if len(shots) > settings.drama_max_shots:
        raise ValueError(f"镜头数 {len(shots)} 超过上限 {settings.drama_max_shots}")
    existing = episode_segments(db, episode)
    if existing and not force:
        raise ValueError("本集已有分段；重新分段会使旧段成片失效，请确认后重试")
    if existing:
        db.query(DramaArtifact).filter(
            DramaArtifact.episode_id == episode.id, DramaArtifact.kind == "segment",
        ).delete(synchronize_session=False)
        for segment in existing:
            db.delete(segment)
        db.flush()
        for shot in shots:
            shot.segment_id = None

    target = float(target_seconds or settings.drama_segment_target_seconds)
    target = max(20.0, min(target, settings.drama_segment_max_seconds))
    groups = _split_shots(
        shots, target, settings.drama_segment_max_seconds, settings.drama_segment_max_shots,
    )
    if len(groups) > MAX_SEGMENTS:
        raise ValueError(f"分段数 {len(groups)} 超过上限 {MAX_SEGMENTS}，请调长段时长")

    scenes = db.query(DramaScene).filter(DramaScene.episode_id == episode.id).all()
    scene_by_no = {s.scene_no: s for s in scenes}
    segments = []
    prev_last_scene = None
    for index, group in enumerate(groups, 1):
        first_scene = scene_by_no.get(group[0].scene_no)
        entry_mode = "cut" if prev_last_scene is None or group[0].scene_no != prev_last_scene else "continue"
        segment = DramaSegment(
            episode_id=episode.id,
            segment_no=index,
            title=f"第 {index} 段 · {first_scene.name if first_scene else ('场景 ' + str(group[0].scene_no))}",
            target_seconds=round(sum(s.duration_s for s in group), 2),
            scene_nos=dump_json(sorted({s.scene_no for s in group})),
            entry_mode=entry_mode,
            status="ready",
        )
        if segments and entry_mode == "continue":
            last = groups[index - 2][-1]
            note = (last.continuity_note or "").strip()
            if not note and last.dialogue:
                note = f"上一段结尾台词：{last.dialogue.strip()[:80]}"
            segment.bridge_note = note
        db.add(segment)
        db.flush()
        for shot in group:
            shot.segment_id = segment.id
        prev_last_scene = group[-1].scene_no
        segments.append(segment)

    db.flush()
    refresh_episode_spec(db, episode)
    return segments


def save_segments(db: Session, episode: DramaEpisode, items: list) -> list:
    """保存手动调整后的段结构；校验覆盖、顺序与上限。"""
    shots = episode_shots(db, episode)
    if not shots:
        raise ValueError("请先规划分镜")
    if not items:
        raise ValueError("分段不能为空")
    by_id = {s.id: s for s in shots}
    seen: list = []
    total_seconds = 0.0
    for item in items:
        ids = list(dict.fromkeys(item.shot_ids))
        if not ids:
            raise ValueError(f"第 {item.segment_no} 段没有镜头")
        if len(ids) > settings.drama_segment_max_shots:
            raise ValueError(
                f"第 {item.segment_no} 段 {len(ids)} 镜，超过上限 {settings.drama_segment_max_shots}"
            )
        group = []
        for shot_id in ids:
            shot = by_id.get(shot_id)
            if shot is None:
                raise ValueError("镜头不属于本集")
            group.append(shot)
        duration = round(sum(s.duration_s for s in group), 2)
        if duration > settings.drama_segment_max_seconds:
            raise ValueError(
                f"第 {item.segment_no} 段 {duration:g} 秒，超过上限 {settings.drama_segment_max_seconds:g} 秒"
            )
        seen.extend(ids)
        total_seconds += duration
    if sorted(seen) != sorted(by_id):
        raise ValueError("分段必须覆盖全部镜头且不能重复")
    if [by_id[i].shot_no for i in seen] != sorted(by_id[i].shot_no for i in seen):
        raise ValueError("段的顺序必须与镜号顺序一致")

    existing = {s.segment_no: s for s in episode_segments(db, episode)}
    keep_nos = set()
    scenes = db.query(DramaScene).filter(DramaScene.episode_id == episode.id).all()
    scene_by_no = {s.scene_no: s for s in scenes}
    prev_last_scene = None
    segments = []
    for item in sorted(items, key=lambda x: x.segment_no):
        group = [by_id[i] for i in item.shot_ids]
        entry_mode = "cut" if prev_last_scene is None or group[0].scene_no != prev_last_scene else "continue"
        segment = existing.get(item.segment_no)
        if segment is None:
            scene = scene_by_no.get(group[0].scene_no)
            segment = DramaSegment(
                episode_id=episode.id, segment_no=item.segment_no,
                title=item.title or f"第 {item.segment_no} 段 · {scene.name if scene else '场景'}",
            )
            db.add(segment)
            db.flush()
        else:
            if item.title:
                segment.title = item.title
        segment.target_seconds = round(sum(s.duration_s for s in group), 2)
        segment.scene_nos = dump_json(sorted({s.scene_no for s in group}))
        segment.entry_mode = entry_mode
        segment.status = "ready"
        if entry_mode == "cut":
            segment.bridge_upload_id = None
            segment.bridge_note = ""
        for shot in group:
            shot.segment_id = segment.id
        prev_last_scene = group[-1].scene_no
        keep_nos.add(segment.segment_no)
        segments.append(segment)

    for no, segment in existing.items():
        if no in keep_nos:
            continue
        has_artifacts = db.query(DramaArtifact).filter(
            DramaArtifact.segment_id == segment.id,
        ).count()
        if has_artifacts:
            raise ValueError(f"第 {no} 段已有成片，不能直接删除；请把镜头并入其他段")
        db.delete(segment)

    db.flush()
    refresh_episode_spec(db, episode)
    return segments


# ---------------------------------------------------------------- 排版

def _compose_plan(db: Session, episode: DramaEpisode, shots: list, mode: str,
                  segment: Optional[DramaSegment] = None) -> dict:
    if not shots:
        raise ValueError("请先规划分镜")
    if len(shots) > settings.drama_max_shots:
        raise ValueError(f"镜头数 {len(shots)} 超过上限 {settings.drama_max_shots}")
    project = db.get(DramaProject, episode.project_id)
    characters = (
        db.query(DramaCharacter)
        .filter(DramaCharacter.project_id == episode.project_id)
        .order_by(DramaCharacter.id)
        .all()
    )
    scenes = db.query(DramaScene).filter(DramaScene.episode_id == episode.id).all()
    scene_by_no = {s.scene_no: s for s in scenes}
    warnings: list = []
    bridge_upload = segment.bridge_upload_id if segment is not None else None

    segments = []
    for i, shot in enumerate(shots):
        if not (settings.drama_min_shot_seconds <= shot.duration_s <= settings.drama_max_shot_seconds):
            raise ValueError(
                f"第 {shot.shot_no} 镜时长需在 {settings.drama_min_shot_seconds:g}–"
                f"{settings.drama_max_shot_seconds:g} 秒（当前 {shot.duration_s:g} 秒）"
            )
        prompt = compose_segment_prompt(shot, characters, scene_by_no.get(shot.scene_no), project)
        refs = collect_ref_images(shot, characters, scene_by_no.get(shot.scene_no), warnings)
        if i == 0 and bridge_upload:
            refs = [int(bridge_upload)] + [r for r in refs if r != int(bridge_upload)]
            if len(refs) > MAX_SEG_IMAGES:
                refs = refs[:MAX_SEG_IMAGES]
                warnings.append("桥接帧优先后参考图超出 9 张上限，已截断")
            prompt = f"[承接] 从上一段落幅自然接续，保持人物、服装与光线一致。\n{prompt}"
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
        "mode": mode,
        "segment_id": segment.id if segment is not None else None,
        "segment_no": segment.segment_no if segment is not None else None,
        "entry_mode": segment.entry_mode if segment is not None else "cut",
        "bridge_upload_id": bridge_upload,
        "warnings": warnings,
    }


def build_plan(db: Session, episode: DramaEpisode) -> dict:
    """整集排版（兼容入口）：按 episode 全部镜头排一次。"""
    return _compose_plan(db, episode, episode_shots(db, episode), "full")


def build_segment_plan(db: Session, segment: DramaSegment) -> dict:
    """段级排版：只排本段镜头，段内仍用 39 帧交叠。"""
    episode = db.get(DramaEpisode, segment.episode_id)
    if episode is None:
        raise ValueError("段所属的剧集不存在")
    shots = segment_shots(db, segment)
    if not shots:
        raise ValueError(f"第 {segment.segment_no} 段没有镜头，请先重新分段")
    return _compose_plan(db, episode, shots, "segment", segment)


def estimate_cost(plan: dict) -> int:
    """与 worker 完全一致的预估计费口径。"""
    duration_int = max(1, round(plan["total_frames"] / DRAMA_FPS))
    return compute_director_cost(duration_int)


def estimate_cost_from_shots(shots: list) -> int:
    """段级快速估算（列表展示用）：按镜头时长 + 段内交叠推算，不构建完整 plan。"""
    frames = 0
    for index, shot in enumerate(shots):
        frames += round(float(shot.duration_s or 0) * DRAMA_FPS)
        if index > 0:
            frames += DRAMA_OVERLAP
    if frames <= 0:
        return 0
    return compute_director_cost(max(1, round(frames / DRAMA_FPS)))


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


def _assets_payload(db: Session, episode: DramaEpisode, shots: list, cache: dict) -> dict:
    """只收被这些镜头引用到的角色/场景，避免无关资产改动把段指纹拖 stale。"""
    project = db.get(DramaProject, episode.project_id)
    char_ids = sorted({
        cid for shot in shots for cid in (load_json(shot.character_ids, []) or [])
    })
    characters = (
        db.query(DramaCharacter).filter(DramaCharacter.id.in_(char_ids)).all()
        if char_ids else []
    )
    scene_nos = sorted({shot.scene_no for shot in shots})
    scenes = (
        db.query(DramaScene)
        .filter(DramaScene.episode_id == episode.id, DramaScene.scene_no.in_(scene_nos))
        .all()
        if scene_nos else []
    )
    return {
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
        } for c in sorted(characters, key=lambda c: c.id)],
        "scenes": [{
            "scene_no": s.scene_no, "name": s.name, "location": s.location,
            "time_of_day": s.time_of_day, "description": s.description,
            "anchor_upload_id": s.anchor_upload_id,
            "anchor_md5": _upload_md5(db, s.anchor_upload_id, cache),
        } for s in sorted(scenes, key=lambda s: s.scene_no)],
    }


def _shots_payload(db: Session, shots: list, cache: dict) -> list:
    return [{
        "shot_no": s.shot_no, "scene_no": s.scene_no, "beat_id": s.beat_id,
        "prompt": s.prompt, "duration_s": s.duration_s,
        "shot_size": s.shot_size, "camera": s.camera, "movement": s.movement,
        "character_ids": sorted(load_json(s.character_ids, []) or []),
        "refs": [{
            "id": uid, "md5": _upload_md5(db, uid, cache),
        } for uid in sorted(load_json(s.ref_upload_ids, []) or [])],
        "dialogue": s.dialogue, "audio_note": s.audio_note,
        "continuity_note": s.continuity_note,
    } for s in shots]


def compute_segment_spec(db: Session, segment: DramaSegment) -> str:
    """段级指纹：只含本段镜头 + 引用资产 + 桥接帧；不含 segment_no。"""
    episode = db.get(DramaEpisode, segment.episode_id)
    shots = segment_shots(db, segment)
    cache: dict = {}
    payload = {
        **_assets_payload(db, episode, shots, cache),
        "entry_mode": segment.entry_mode,
        "bridge_md5": (
            _upload_md5(db, segment.bridge_upload_id, cache)
            if segment.entry_mode == "continue" else ""
        ),
        "shots": _shots_payload(db, shots, cache),
    }
    return hashlib.sha1(_canonical(payload).encode("utf-8")).hexdigest()


def _legacy_spec(db: Session, episode: DramaEpisode) -> str:
    """未分段的旧集：保持整集哈希（仅迁移/异常时使用）。"""
    shots = episode_shots(db, episode)
    cache: dict = {}
    payload = {
        **_assets_payload(db, episode, shots, cache),
        "shots": _shots_payload(db, shots, cache),
    }
    return hashlib.sha1(_canonical(payload).encode("utf-8")).hexdigest()


def compute_spec_hash(db: Session, episode: DramaEpisode) -> str:
    segments = episode_segments(db, episode)
    if not segments:
        return _legacy_spec(db, episode)
    for segment in segments:
        if not segment.spec_hash:
            segment.spec_hash = compute_segment_spec(db, segment)
    if len(segments) == 1:
        return segments[0].spec_hash
    payload = [{"no": s.segment_no, "hash": s.spec_hash} for s in segments]
    return hashlib.sha1(_canonical(payload).encode("utf-8")).hexdigest()


def refresh_episode_spec(db: Session, episode: DramaEpisode) -> str:
    """重算段指纹与集指纹；镜头指纹 = 所属段指纹（便于 clip stale 比较）。"""
    segments = episode_segments(db, episode)
    if segments:
        for segment in segments:
            segment.spec_hash = compute_segment_spec(db, segment)
            for shot in segment_shots(db, segment):
                shot.spec_hash = segment.spec_hash
        episode.spec_hash = compute_spec_hash(db, episode)
    else:
        episode.spec_hash = _legacy_spec(db, episode)
        for shot in episode_shots(db, episode):
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

    segments = episode_segments(db, episode)
    plans: list = []
    est_cost = 0
    total_seconds = round(sum(s.duration_s for s in shots), 2)
    if shots:
        if not segments or any(s.segment_id is None for s in shots):
            issues.append(_item("SEGMENT_MISSING", "分镜已改动，请先重新分段再生成"))
        else:
            try:
                total_frames = 0
                for segment in segments:
                    if not segment_shots(db, segment):
                        issues.append(_item(
                            "SEGMENT_EMPTY", f"第 {segment.segment_no} 段没有镜头",
                        ))
                        continue
                    plan = build_segment_plan(db, segment)
                    plans.append(plan)
                    total_frames += plan["total_frames"]
                    est_cost += estimate_cost(plan)
                    warnings.extend(_item("SHOT_REF_LIMIT", w) for w in plan.get("warnings", []))
                total_seconds = round(total_frames / DRAMA_FPS, 2) if plans else total_seconds
            except (ValueError, AssertionError, KeyError) as exc:
                issues.append(_item("PLAN_FAILED", f"分段排版失败：{exc}"))

    for segment in segments:
        if segment.target_seconds and segment.target_seconds < MIN_SEGMENT_SECONDS:
            warnings.append(_item(
                "SEGMENT_SHORT",
                f"第 {segment.segment_no} 段仅 {segment.target_seconds:g} 秒，建议与相邻同场段合并",
            ))
        if segment.entry_mode == "continue" and not segment.bridge_upload_id:
            warnings.append(_item(
                "SEGMENT_BRIDGE_MISSING",
                f"第 {segment.segment_no} 段为承接段，生成时自动从上一段末帧建立桥接",
            ))

    if plans and total_seconds > settings.drama_max_episode_seconds:
        issues.append(_item(
            "TOTAL_SECONDS", f"本集 {total_seconds:g} 秒，超过上限 {settings.drama_max_episode_seconds} 秒",
        ))

    target = sum(float(b.get("target_seconds") or 0) for b in beats)
    if plans and target > 0 and abs(total_seconds - target) / target > 0.2:
        drift = round((total_seconds - target) / target * 100)
        warnings.append(_item("DURATION_DRIFT", f"总时长与目标偏差 {drift:+d}%"))

    actor = user or db.get(User, db.get(DramaProject, episode.project_id).user_id)
    balance = actor.credits if actor else 0
    if est_cost and balance < est_cost:
        warnings.append(_item("INSUFFICIENT_CREDITS", f"余额不足，生成前需充值（差 {est_cost - balance}）"))

    largest = max((p["total_frames"] for p in plans), default=0)
    return {
        "ready": not issues,
        "shots": len(shots),
        "segments": len(segments),
        "total_seconds": total_seconds,
        "est_cost": est_cost,
        "balance": balance,
        "sufficient": balance >= est_cost,
        "issues": issues,
        "warnings": warnings,
        "plan": None if not plans else {
            "total_frames": sum(p["total_frames"] for p in plans),
            "fps": DRAMA_FPS,
            "max_segment_frames": largest,
            "segments": [{
                "segment_no": p["segment_no"],
                "shot_no": seg["shot_no"],
                "frames": seg["frames"],
                "visible_frames": seg["visible_frames"],
                "ref_count": seg["ref_count"],
                "prompt": seg["prompt"],
            } for p in plans for seg in p["segments"]],
            "windows": [w for p in plans for w in p["windows"]],
        },
    }

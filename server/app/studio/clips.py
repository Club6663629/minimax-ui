"""段视频：一段一次 r2v，关键帧按切序当参考图。

- 关键帧必须齐：h3Prompt 里 `Picture N aligns with X-second mark` 与切序一一对应，
  缺一张整段就错位，所以缺图直接拦下、不出半成品；
- 参考图走 Upload 行（slot=studio）喂给现有 r2v 链路，素材库不展示这些内部图；
- 提示词就是段上的 h3Prompt（上游 h3-structure 门保证三段式），enhance=False 不二次改写；
- 时长取段总秒数四舍五入并夹到 5–15；实际帧数由 H3 吸附（5+17n），成片以生成结果为准。
"""
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Task, Upload
from . import docs as docmod
from .keyframes import _episode_doc
from .models import StudioKeyframe, StudioSegmentClip

MAX_REFS = 9
MIN_SEC, MAX_SEC = 5, 15

# 任务状态 → 段状态。任务完成后才有视频路径。
STATUS_MAP = {
    "queued": "queued",
    "enhancing": "running",
    "generating_768p": "running",
    "upscaling": "running",
    "done": "ready",
    "failed": "failed",
    "cancelled": "failed",
}


def segments_of(board: dict, ep_no: int) -> dict:
    """该集切表的段索引：{段号: 段}。"""
    ep_doc = _episode_doc(board, ep_no)
    return {str(s.get("id")): s for s in (ep_doc.get("segments") or [])}


def segment_of(board: dict, ep_no: int, segment_id: str) -> dict:
    """从切表里取一段；不在表里直接报错（路由转 404）。"""
    ep_doc = _episode_doc(board, ep_no)
    seg = next(
        (s for s in (ep_doc.get("segments") or []) if s.get("id") == segment_id), None
    )
    if seg is None:
        raise ValueError(f"这一段不在切表里：{segment_id}")
    return seg


def seconds_of(seg: dict) -> float:
    return round(sum(float(c.get("seconds") or 0) for c in seg.get("cuts") or []), 1)


def duration_of(seconds: float) -> int:
    """平台档位只认整数秒；短段抬到 5、长段压到 15（上游 segment-cap 本就不会超）。"""
    return max(MIN_SEC, min(MAX_SEC, int(round(float(seconds)))))


def _upload_for(db: Session, user, kf: StudioKeyframe) -> Optional[Upload]:
    """关键帧 → Upload 行。同一张关键帧复用一行，重出后刷新路径与指纹。"""
    path = Path(kf.path or "")
    if not path.exists():
        return None
    name = f"studio-kf-{kf.id}.png"
    row = (
        db.query(Upload)
        .filter(Upload.user_id == user.id, Upload.filename == name)
        .first()
    )
    if row is None:
        row = Upload(user_id=user.id, slot="studio", filename=name)
        db.add(row)
    row.path = str(path)
    row.md5 = (kf.sha256 or "")[:32]
    row.size = path.stat().st_size
    return row


def refs_for_segment(
    db: Session, user, episode_id: int, segment_id: str, cut_count: int
) -> tuple:
    """按切序取关键帧 → Upload 列表。(uploads, 缺失项)。"""
    rows = (
        db.query(StudioKeyframe)
        .filter(
            StudioKeyframe.episode_id == episode_id,
            StudioKeyframe.segment_id == segment_id,
        )
        .all()
    )
    by_index = {r.cut_index: r for r in rows}
    uploads: list = []
    missing: list = []
    for i in range(1, cut_count + 1):
        row = by_index.get(i)
        if row is None or row.status != "ready":
            missing.append(f"切 {i} 关键帧没出")
            continue
        upload = _upload_for(db, user, row)
        if upload is None:
            missing.append(f"切 {i} 关键帧文件丢了")
            continue
        uploads.append(upload)
    return uploads, missing


def plan(db: Session, user, project, episode, segment_id: str) -> dict:
    """一段的排单要素：提示词、时长、参考图，以及缺什么。"""
    board = docmod.get_doc(db, project.id, "storyboard", episode.id) or {}
    seg = segment_of(board, episode.ep_no, segment_id)
    cuts = seg.get("cuts") or []
    seconds = seconds_of(seg)
    uploads, missing = refs_for_segment(db, user, episode.id, segment_id, len(cuts))
    if len(cuts) > MAX_REFS:
        missing.append(f"这一段 {len(cuts)} 切，超过 r2v 参考图上限 {MAX_REFS}")
    return {
        "segment": seg,
        "prompt": str(seg.get("h3Prompt") or "").strip(),
        "seconds": seconds,
        "duration": duration_of(seconds),
        "cut_count": len(cuts),
        "uploads": uploads,
        "missing": missing,
    }


def clip_of(db: Session, episode_id: int, segment_id: str) -> Optional[StudioSegmentClip]:
    return (
        db.query(StudioSegmentClip)
        .filter(
            StudioSegmentClip.episode_id == episode_id,
            StudioSegmentClip.segment_id == segment_id,
        )
        .first()
    )


def clips_for_episode(db: Session, episode_id: int) -> list:
    return (
        db.query(StudioSegmentClip)
        .filter(StudioSegmentClip.episode_id == episode_id)
        .order_by(StudioSegmentClip.segment_id)
        .all()
    )


def sync_clip(db: Session, clip: StudioSegmentClip) -> StudioSegmentClip:
    """按任务状态回写段状态；只在有变化时改内存对象，提交交给调用方。"""
    if not clip.task_id:
        return clip
    task = db.get(Task, int(clip.task_id))
    if task is None:
        return clip
    status = STATUS_MAP.get(str(task.status), "running")
    path = str(task.video_path or "") if status == "ready" else ""
    error = str(task.error or "")[:500] if status == "failed" else ""
    if (clip.status, clip.path, clip.error) != (status, path, error):
        clip.status = status
        clip.path = path
        clip.error = error
    return clip


def out_of(clip: StudioSegmentClip, segment: Optional[dict] = None) -> dict:
    """前端用：带上切表里的秒数与切数，方便直接判断进度。"""
    seg = segment or {}
    return {
        "id": clip.id,
        "segment_id": clip.segment_id,
        "status": clip.status,
        "task_id": clip.task_id,
        "duration_sec": clip.duration_sec,
        "seconds": seconds_of(seg) if seg else 0.0,
        "cut_count": len(seg.get("cuts") or []) if seg else 0,
        "url": f"/files/studio/clip/{clip.id}" if clip.status == "ready" and clip.path else "",
        "error": clip.error,
    }

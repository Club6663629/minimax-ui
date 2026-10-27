"""成片：按切表顺序把段视频拼成一条。

- 只有全部段 ready 才允许合成（缺一段就是缺剧情的片子，不给半成品）；
- 拼接复用 media.concat_clips：优先 -c copy，时长偏差或音轨不一致时自动重编码；
- cutlist 落每段的计划秒数、实测时长与文件，交付可追溯；
- 同步执行：5 段、11 秒左右的段通常 1–3 秒拼完，不值得再开一条队列。
"""
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import STUDIO_DIR
from ..services import media
from . import clips as clipmod
from . import docs as docmod
from .models import StudioRender

logger = logging.getLogger(__name__)


def output_path(project_id: int, ep_no: int) -> Path:
    return (
        STUDIO_DIR
        / "projects"
        / str(int(project_id))
        / f"ep{int(ep_no):02d}"
        / "final.mp4"
    )


def cutlist_of(db: Session, project, episode) -> tuple:
    """按切表顺序排好的段清单。(cutlist, blockers)。"""
    board = docmod.get_doc(db, project.id, "storyboard", episode.id) or {}
    segs = clipmod.segments_of(board, episode.ep_no)
    if not segs:
        return [], ["还没有切表，先分段再生成切表"]

    clips = {
        c.segment_id: c
        for c in clipmod.clips_for_episode(db, episode.id)
    }
    cutlist: list = []
    blockers: list = []
    for index, (segment_id, seg) in enumerate(segs.items(), start=1):
        clip = clips.get(segment_id)
        if clip is None or clip.status != "ready":
            blockers.append(f"{segment_id} 还没出片")
            continue
        path = Path(clip.path or "")
        if not path.exists():
            blockers.append(f"{segment_id} 的视频文件丢了")
            continue
        cutlist.append(
            {
                "index": index,
                "segment_id": segment_id,
                "file": str(path),
                "planned_sec": clipmod.seconds_of(seg),
                "actual_sec": round(media.probe_duration(path), 2),
            }
        )
    return cutlist, blockers


def latest(db: Session, episode_id: int) -> Optional[StudioRender]:
    return (
        db.query(StudioRender)
        .filter(StudioRender.episode_id == episode_id)
        .order_by(StudioRender.id.desc())
        .first()
    )


def do_render(db: Session, project, episode) -> StudioRender:
    """拼一条成片：先落状态再跑 ffmpeg，失败也留痕。"""
    cutlist, blockers = cutlist_of(db, project, episode)
    if blockers:
        raise ValueError("；".join(blockers))

    out = output_path(project.id, episode.ep_no)
    row = StudioRender(episode_id=episode.id, path="", status="rendering")
    db.add(row)
    db.commit()
    db.refresh(row)
    try:
        media.concat_clips([Path(c["file"]) for c in cutlist], out, fps=24)
        row.path = str(out)
        row.duration_sec = int(round(media.probe_duration(out)))
        row.cutlist = docmod.dumps(cutlist)
        row.status = "done"
        row.error = ""
        logger.info(
            "studio render done project=%s ep=%s segments=%s duration=%ss",
            project.id, episode.ep_no, len(cutlist), row.duration_sec,
        )
    except Exception as exc:  # noqa: BLE001 —— ffmpeg 的失败形态很多，统一落 error
        row.status = "failed"
        row.error = str(exc)[:500]
        logger.warning("studio render failed project=%s ep=%s: %s", project.id, episode.ep_no, exc)
    db.commit()
    db.refresh(row)
    return row


def out_of(row: Optional[StudioRender]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "id": row.id,
        "status": row.status,
        "url": f"/files/studio/render/{row.id}" if row.status == "done" and row.path else "",
        "duration_sec": row.duration_sec,
        "error": row.error,
        "cutlist": docmod.loads(row.cutlist, []) or [],
    }

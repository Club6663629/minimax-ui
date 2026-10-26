"""六份 JSON 的读写与上下文装配。

原则：文档是唯一事实来源。库里存的是 `json.dumps(..., ensure_ascii=False)`，
读出来直接给门和前端，不做二次结构转换。
"""
import json
from typing import Optional

from sqlalchemy.orm import Session

from .models import StudioDocument, StudioEpisode, StudioProject

SERIES_KINDS = {"outline", "cast", "art", "series"}
EPISODE_KINDS = {"script", "staging", "storyboard"}

EMPTY_DOCS: dict = {
    "outline": {"source": "", "episodes": [], "characters": []},
    "script": {"source": "", "episodes": []},
    "cast": {"source": "", "characters": []},
    "art": {"source": "", "scenes": [], "props": []},
    "staging": {"source": "", "lang": "zh", "locations": [], "scenes": []},
    "storyboard": {
        "source": "",
        "aspectRatio": "9:16",
        "promptLang": "en",
        "params": {
            "maxSegmentSeconds": 15,
            "minCutSeconds": 2,
            "maxCutSeconds": 5,
            "maxOnScreen": 3,
            "tolerance": 0.15,
        },
        "episodes": [],
    },
    "series": {"title": "", "script": {}, "art": {}},
}


def loads(text: str, default=None):
    """坏 JSON 不抛：返回 default，让调用方决定怎么报。"""
    try:
        value = json.loads(text or "null")
    except (TypeError, ValueError):
        return default
    return default if value is None else value


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def scope_of(kind: str, episode_id: int = 0) -> int:
    """系列级文档统一存 episode_id=0，避免 NULL 在唯一约束里重复。"""
    return 0 if kind in SERIES_KINDS else int(episode_id or 0)


def get_doc(db: Session, project_id: int, kind: str, episode_id: int = 0) -> dict:
    row = (
        db.query(StudioDocument)
        .filter(
            StudioDocument.project_id == project_id,
            StudioDocument.episode_id == scope_of(kind, episode_id),
            StudioDocument.kind == kind,
        )
        .one_or_none()
    )
    if not row:
        return json.loads(json.dumps(EMPTY_DOCS.get(kind, {})))
    return loads(row.data, json.loads(json.dumps(EMPTY_DOCS.get(kind, {}))))


def get_document_row(
    db: Session, project_id: int, kind: str, episode_id: int = 0
) -> Optional[StudioDocument]:
    return (
        db.query(StudioDocument)
        .filter(
            StudioDocument.project_id == project_id,
            StudioDocument.episode_id == scope_of(kind, episode_id),
            StudioDocument.kind == kind,
        )
        .one_or_none()
    )


def put_doc(
    db: Session, project_id: int, kind: str, data, episode_id: int = 0
) -> StudioDocument:
    row = get_document_row(db, project_id, kind, episode_id)
    if row is None:
        row = StudioDocument(
            project_id=project_id,
            episode_id=scope_of(kind, episode_id),
            kind=kind,
            data=dumps(data),
        )
        db.add(row)
    else:
        row.data = dumps(data)
    db.commit()
    db.refresh(row)
    return row


def project_ctx(db: Session, project_id: int) -> dict:
    """门要的上下文：outline / cast / art。缺哪份给空，门自己会跳过。"""
    return {
        "outline": get_doc(db, project_id, "outline"),
        "cast": get_doc(db, project_id, "cast"),
        "art": get_doc(db, project_id, "art"),
    }


def episode_ctx(db: Session, episode: StudioEpisode) -> dict:
    """一集的门上下文：系列三份 + 本集 script。"""
    ctx = project_ctx(db, episode.project_id)
    ctx["script"] = get_doc(db, episode.project_id, "script", episode.id)
    return ctx


def episode_ref(db: Session, project_id: int, ep_no: int) -> Optional[StudioEpisode]:
    return (
        db.query(StudioEpisode)
        .filter(StudioEpisode.project_id == project_id, StudioEpisode.ep_no == ep_no)
        .one_or_none()
    )


def episode_of_doc(db: Session, project_id: int, kind: str) -> list:
    """系列级文档之外，按集列出已存在的文档（剧本视图用）。"""
    rows = (
        db.query(StudioDocument)
        .filter(
            StudioDocument.project_id == project_id,
            StudioDocument.kind == kind,
            StudioDocument.episode_id != 0,
        )
        .all()
    )
    return rows


def project_or_404(db: Session, project_id: int, user_id: Optional[int] = None) -> StudioProject:
    query = db.query(StudioProject).filter(StudioProject.id == project_id)
    if user_id is not None:
        query = query.filter(StudioProject.user_id == user_id)
    project = query.one_or_none()
    if project is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def episode_or_404(db: Session, episode_id: int) -> StudioEpisode:
    episode = db.get(StudioEpisode, episode_id)
    if episode is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="分集不存在")
    return episode

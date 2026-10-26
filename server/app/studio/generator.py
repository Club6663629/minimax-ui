"""分段与切表：把 script.json 确定性展开成 staging / storyboard。

没有「编剧模型」这一步——分段、切长、景别、H3 提示词骨架都由
`services/skills/shuohao/studio_seed.mjs` 按上游规则算，这里只负责装配输入、
落库、回跑门，让前端能立刻看到「哪几道门没过、差在哪」。
"""
import json
from typing import Optional

from sqlalchemy.orm import Session

from . import docs as docmod
from . import gates as gatemod
from .models import StudioEpisode, StudioProject

KINDS = ("staging", "storyboard")
# 生成成功后推进的分集状态；只前进，不回退
NEXT_STATUS = {"staging": "staged", "storyboard": "boarded"}
ORDER = ["imported", "scripted", "staged", "boarded", "keyframed", "rendering", "done"]


def look_of(project: StudioProject) -> dict:
    """项目级视觉风格（导入时从美术设定集的视觉关键词抽出）；坏 JSON 当空。"""
    try:
        look = json.loads(project.look or "{}")
    except ValueError:
        return {}
    return look if isinstance(look, dict) else {}


def payload_of(db: Session, project: StudioProject, episode: StudioEpisode) -> dict:
    """生成器的输入：本集剧本 + 系列级三份设定 + 项目 look。"""
    ctx = docmod.project_ctx(db, project.id)
    return {
        "script": docmod.get_doc(db, project.id, "script", episode.id),
        "art": ctx.get("art") or {},
        "cast": ctx.get("cast") or {},
        "outline": ctx.get("outline") or {},
        "look": look_of(project),
        "ep": int(episode.ep_no),
    }


def generate(
    db: Session,
    project: StudioProject,
    episode: StudioEpisode,
    kind: str,
    *,
    data: Optional[dict] = None,
) -> dict:
    """生成并落库，返回 {doc, gates}。

    data 不为空时用前端传上来的剧本，不读库——「改一句就重生成」不用先保存。
    """
    if kind not in KINDS:
        raise ValueError(f"只能生成 {' / '.join(KINDS)}，收到 {kind}")

    payload = payload_of(db, project, episode)
    if data is not None:
        payload["script"] = data
    script = payload.get("script") or {}
    episodes = script.get("episodes") or []
    if not any(int(e.get("ep") or 0) == int(episode.ep_no) for e in episodes):
        raise ValueError(f"剧本里没有第 {episode.ep_no} 集，先生成剧本")

    doc = gatemod.run_seed(kind, payload)
    docmod.put_doc(db, project.id, kind, doc, episode.id)

    want = NEXT_STATUS[kind]
    if ORDER.index(want) > ORDER.index(episode.status or "imported"):
        episode.status = want
        db.commit()

    result = gatemod.check(db, project, kind, episode=episode)
    return {"doc": doc, "gates": result}


def summary(doc: dict, kind: str = "storyboard") -> dict:
    """给前端用的概览：切表给段/切/秒，分段给场/地点。"""
    if kind == "staging":
        return {
            "scenes": len(doc.get("scenes") or []),
            "locations": len(doc.get("locations") or []),
        }
    segments = (doc.get("episodes") or [{}])[0].get("segments") or []
    cuts = [c for s in segments for c in (s.get("cuts") or [])]
    seconds = round(sum(float(c.get("seconds") or 0) for c in cuts), 2)
    return {
        "segments": len(segments),
        "cuts": len(cuts),
        "seconds": seconds,
        "sceneIndexes": sorted({int(s.get("sceneIndex") or 0) for s in segments}),
    }

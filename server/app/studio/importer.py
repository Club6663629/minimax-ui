"""导入编排：解析原始 Markdown → 建集 → 存六份 JSON → 建资产。

一次导入是可重入的：force=True 时覆盖已有文档与集，资产按 (kind, ref_id) upsert，
已出的设定图（studio_asset_views）不动——重导入不该把已经画好的定妆图冲掉。
"""
from typing import Optional

from sqlalchemy.orm import Session

from . import docs as docmod
from .intake import build_all
from .models import StudioAsset, StudioEpisode, StudioProject


def _upsert_asset(
    db: Session, project_id: int, kind: str, ref_id: str, name: str, data: dict
) -> StudioAsset:
    row = (
        db.query(StudioAsset)
        .filter(
            StudioAsset.project_id == project_id,
            StudioAsset.kind == kind,
            StudioAsset.ref_id == ref_id,
        )
        .one_or_none()
    )
    if row is None:
        row = StudioAsset(project_id=project_id, kind=kind, ref_id=ref_id, name=name)
        db.add(row)
    row.name = name
    row.data = docmod.dumps(data)
    return row


def import_project(
    db: Session,
    project: StudioProject,
    script_md: str,
    art_md: str,
    force: bool = False,
) -> dict:
    """把两份 Markdown 落成一个项目。返回一眼能看懂的统计。"""
    if project.status != "draft" and not force:
        raise ValueError("项目已导入；确认覆盖请带 force=true")

    parsed = build_all(script_md, art_md, project.title)
    script_doc = parsed["script"]
    params = script_doc.get("params") or {}

    # 1. 建集：集号是剧本里的集号，name 先用钩子，M1 允许前端改
    episodes: dict = {}
    for ep in script_doc["episodes"]:
        ep_no = ep["ep"]
        row = docmod.episode_ref(db, project.id, ep_no)
        if row is None:
            row = StudioEpisode(project_id=project.id, ep_no=ep_no)
            db.add(row)
        row.name = (ep.get("hook") or f"第 {ep_no} 集")[:120]
        row.status = "imported"
        episodes[ep_no] = row
    db.commit()
    for row in episodes.values():
        db.refresh(row)

    # 2. 系列级文档
    docmod.put_doc(db, project.id, "outline", parsed["outline"])
    docmod.put_doc(db, project.id, "cast", parsed["cast"])
    docmod.put_doc(db, project.id, "art", parsed["art"])
    docmod.put_doc(db, project.id, "series", parsed["series"])

    # 3. 每集一份 script：单集流水线只读自己那份，不背 80 集的包袱
    for ep in script_doc["episodes"]:
        row = episodes[ep["ep"]]
        docmod.put_doc(
            db,
            project.id,
            "script",
            {
                "source": script_doc.get("source", ""),
                "lang": "zh",
                "params": params,
                "episodes": [ep],
            },
            row.id,
        )

    # 4. 资产：角色 / 场景 / 道具
    # cast.json 的角色不带 id（上游靠名字对账），资产的 ref_id 用 people_map 反查
    cid_of = {name: cid for name, cid in (parsed.get("people") or {}).items()}
    for c in parsed["cast"].get("characters") or []:
        ref = cid_of.get(c.get("name"), c.get("name", ""))
        _upsert_asset(db, project.id, "character", ref, c["name"], c)
    for s in parsed["art"].get("scenes") or []:
        _upsert_asset(db, project.id, "scene", s["id"], s["name"], s)
    for p in parsed["art"].get("props") or []:
        _upsert_asset(db, project.id, "prop", p["id"], p["name"], p)

    # 5. 项目快照：画风冻结在项目上
    look = parsed["series"].get("look") or {}
    project.look = docmod.dumps(look)
    project.status = "imported"
    project.source_name = "焚霜引" if "焚霜引" in script_md[:2000] else project.source_name
    db.commit()

    return {
        "episodes": len(episodes),
        "characters": len(parsed["cast"].get("characters") or []),
        "scenes": len(parsed["art"].get("scenes") or []),
        "props": len(parsed["art"].get("props") or []),
        "beats": len(parsed["outline"].get("beats") or []),
        "people": len(parsed["people"]),
    }


def episode_rows(db: Session, project_id: int) -> list:
    return (
        db.query(StudioEpisode)
        .filter(StudioEpisode.project_id == project_id)
        .order_by(StudioEpisode.ep_no)
        .all()
    )


def episode_stats(db: Session, project_id: int) -> dict:
    """导入后给前端的地图：每集有几场、几切、切表总秒数。"""
    rows = episode_rows(db, project_id)
    counts = {}
    for row in rows:
        script = docmod.get_doc(db, project_id, "script", row.id)
        eps = script.get("episodes") or []
        scenes = (eps[0].get("scenes") if eps else []) or []
        board = docmod.get_doc(db, project_id, "storyboard", row.id) or {}
        segments = (board.get("episodes") or [{}])[0].get("segments") or []
        cuts = [c for seg in segments for c in (seg.get("cuts") or [])]
        counts[row.ep_no] = {
            "scenes": len(scenes),
            "seconds": (eps[0].get("targetSeconds") if eps else 0) or 0,
            "cuts": len(cuts),
            "cut_seconds": round(sum(float(c.get("seconds") or 0) for c in cuts), 2),
        }
    return counts

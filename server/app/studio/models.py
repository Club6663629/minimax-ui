"""剧集工坊数据表。

与 shuohao 的对应关系：
- 六份 JSON（outline/script/cast/art/staging/storyboard）→ `studio_documents`
- 角色/场景/道具（含四视图）→ `studio_assets` + `studio_asset_views`
- 每切一张关键帧 → `studio_keyframes`
- 每一次生成（图/段/成片）→ `studio_jobs`，外键指向现有 `tasks.id`
- 段与切不建表：从 storyboard.json 派生，避免两份数据打架
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class StudioProject(Base):
    """一部剧。导入一次剧本 + 美术设定，产出 80 集地图与系列设定。"""

    __tablename__ = "studio_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    source_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | imported | active | done
    # 画风快照（shuohao 的 look）：导入时写一次，出图时冻结
    look: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioEpisode(Base):
    """一集。ep_no 是剧本里的集号（1 起），与 storyboard 的 ep 对齐。"""

    __tablename__ = "studio_episodes"
    __table_args__ = (UniqueConstraint("project_id", "ep_no", name="uq_studio_episode_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    ep_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120), default="")
    # imported → scripted → staged → boarded → keyframed → rendering → done
    status: Mapped[str] = mapped_column(String(16), default="imported")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioDocument(Base):
    """一份 JSON。episode_id=0 表示系列级（outline/cast/art/series）。"""

    __tablename__ = "studio_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "episode_id", "kind", name="uq_studio_document"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    episode_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # outline|script|cast|art|staging|storyboard|series
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioAsset(Base):
    """角色 / 场景 / 道具。ref_id 是 shuohao 编码（C01/S01/P01）。"""

    __tablename__ = "studio_assets"
    __table_args__ = (
        UniqueConstraint("project_id", "kind", "ref_id", name="uq_studio_asset_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # character | scene | prop
    ref_id: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    # cast.json / art.json 里的单条记录（含 views 之外的文字层）
    data: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | imaging | ready
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioAssetView(Base):
    """一张设定图。view 用 shuohao 的名字：front-full / face-front / side-full / back-full / detail-*。"""

    __tablename__ = "studio_asset_views"
    __table_args__ = (
        UniqueConstraint("asset_id", "view", "version", name="uq_studio_asset_view"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("studio_assets.id"), index=True)
    view: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer, default=1)
    path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    model: Mapped[str] = mapped_column(String(40), default="qwen")
    prompt: Mapped[str] = mapped_column(Text, default="")
    refs: Mapped[str] = mapped_column(Text, default="[]")   # [{view,v,sha256}]
    gates: Mapped[str] = mapped_column(Text, default="[]")  # shuohao 的 size/white/...
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class StudioKeyframe(Base):
    """一切一张关键帧。segment_id 形如 E01-01，cut_index 从 1 起。"""

    __tablename__ = "studio_keyframes"
    __table_args__ = (
        UniqueConstraint("episode_id", "segment_id", "cut_index", name="uq_studio_keyframe"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("studio_episodes.id"), index=True)
    segment_id: Mapped[str] = mapped_column(String(16), index=True)
    cut_index: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(512), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    refs: Mapped[str] = mapped_column(Text, default="[]")  # 实际挂的设定图
    status: Mapped[str] = mapped_column(String(12), default="missing")  # missing | queued | ready | stale
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioSegmentClip(Base):
    """一段成片：一段一次 r2v，关键帧按切序当参考图。

    status 不单独维护：每次读取都按 task_id 对应的任务状态映射，
    避免任务被重投/退款后两边状态漂移。
    """

    __tablename__ = "studio_segment_clips"
    __table_args__ = (
        UniqueConstraint("episode_id", "segment_id", name="uq_studio_segment_clip"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("studio_episodes.id"), index=True)
    segment_id: Mapped[str] = mapped_column(String(16), index=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    path: Mapped[str] = mapped_column(String(512), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="missing")  # missing|queued|running|ready|failed
    prompt: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioJob(Base):
    """一次生成。task_id 指向现有 Task；target_key 是资产/段/切的业务键。"""

    __tablename__ = "studio_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("studio_projects.id"), index=True)
    episode_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    kind: Mapped[str] = mapped_column(String(12))         # image | video | render
    target_type: Mapped[str] = mapped_column(String(12))  # asset | keyframe | segment | render
    target_key: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed|cancelled
    cost: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class StudioRender(Base):
    """一集成片：拼接、字幕、响度与交付清单。"""

    __tablename__ = "studio_renders"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("studio_episodes.id"), index=True)
    path: Mapped[str] = mapped_column(String(512), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|rendering|done|failed
    cutlist: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

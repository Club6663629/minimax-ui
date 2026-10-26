"""剧集工坊请求/响应模型。"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

DocKind = Literal["outline", "script", "cast", "art", "staging", "storyboard", "series"]


class ProjectCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    look: Optional[dict] = None


class ProjectPatchIn(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    look: Optional[dict] = None


class ImportIn(BaseModel):
    """导入原始文档。两份都可单独给，给全了才会建 80 集地图。"""

    script_md: str = ""
    art_md: str = ""
    force: bool = False


class DocumentIn(BaseModel):
    data: Any
    # 乐观锁：前端拿到的 updated_at 原样回传，变了就 409
    base_updated_at: Optional[str] = None


class GatesIn(BaseModel):
    kind: DocKind
    data: Optional[Any] = None  # 不传则用库里已存的


class GateOut(BaseModel):
    id: str
    label: str
    ok: bool
    detail: str = ""


class GatesOut(BaseModel):
    kind: str
    gates: list[GateOut]
    problems: list[str] = Field(default_factory=list)
    stats: Optional[dict] = None
    passed: bool = False


class ImageJobIn(BaseModel):
    """出设定图：asset_id + 要出的视图；或直接给 keyframe 目标。"""

    target: Literal["asset", "keyframe"]
    asset_id: Optional[int] = None
    view: Optional[str] = None            # front-full / face-front / side-full / back-full / detail-*
    keyframe: Optional[str] = None        # E01-01:3
    prompt: str = ""
    refs: list[str] = Field(default_factory=list)  # 参考图 upload id 或资产图 key
    count: int = 1


class AssetImageIn(BaseModel):
    """出一张设定图。view 留空 = 该资产的首选视图（角色正面全身 / 场景道具 sheet）。"""

    view: str = ""
    version: int = 0        # 0 = 自动取「当前最大版本 + 1」，重出不覆盖旧版
    prompt: str = ""        # 留空 = 用 prompts.build 按视图拼装
    negative: str = ""
    seed: Optional[int] = None


class VideoJobIn(BaseModel):
    segment_id: str
    cut_index: Optional[int] = None       # 只重跑某一切时给


class RenderIn(BaseModel):
    burn_subtitles: bool = True
    normalize_audio: bool = True


class JobOut(BaseModel):
    id: int
    kind: str
    target_type: str
    target_key: str
    task_id: Optional[int] = None
    status: str
    cost: int
    error: str = ""
    created_at: str = ""

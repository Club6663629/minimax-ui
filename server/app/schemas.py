"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ---- 认证 ----
class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=6, max_length=64)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    username: str
    role: str
    credits: int
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- 上传 ----
class UploadOut(BaseModel):
    id: int
    slot: str
    filename: str
    url: str


# ---- 视频任务 ----
class VideoCreateIn(BaseModel):
    mode: Literal["t2v", "flf2v", "r2v"]
    prompt: str = Field(min_length=1, max_length=2000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    duration: Literal[5, 8, 10, 15] = 5
    resolution: Literal["768p", "1k", "2k"] = "768p"
    enhance: bool = True
    scene: Literal["general", "drama", "ecommerce"] = "general"
    first_image_id: Optional[int] = None
    last_image_id: Optional[int] = None
    # 全能参考资料 id：图片/视频/音频混存，总数上限 9（其中视频 ≤3、音频 ≤3）
    ref_image_ids: list[int] = Field(default_factory=list, max_length=9)


class TaskOut(BaseModel):
    id: int
    mode: str
    prompt: str
    enhanced_prompt: str
    aspect_ratio: str
    duration: int
    resolution: str
    enhance: bool
    scene: str
    status: str
    error: str
    cost: int
    video_url: Optional[str]
    upscale_urls: dict[str, str] = {}  # {"1k": url, "2k": url} 按分辨率下载
    first_image_url: Optional[str]
    last_image_url: Optional[str]
    ref_image_urls: list[str]
    ref_video_urls: list[str] = []
    ref_audio_urls: list[str] = []
    parent_task_id: Optional[int] = None
    upscale_target: Optional[str] = None
    worker_url: str = ""
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    # 管理后台列表附带
    user_email: Optional[str] = None


# ---- 积分 / 充值 ----
class CreditLogOut(BaseModel):
    id: int
    amount: int
    type: str
    note: str
    task_id: Optional[int]
    created_at: datetime


class RedeemIn(BaseModel):
    code: str


class UpgradeIn(BaseModel):
    resolution: Literal["1k", "2k"]


class PackageOut(BaseModel):
    name: str
    credits: int
    price: str
    tag: str
    description: str


class PricingOut(BaseModel):
    signup_bonus: int
    cost_768p_5s: int
    cost_768p_8s: int
    cost_768p_10s: int
    cost_768p_15s: int
    cost_1k_extra: int
    cost_2k_extra: int
    cloud_enabled: bool
    upscale_enabled: bool
    packages: list[PackageOut]


# ---- 管理后台 ----
class AdminAdjustIn(BaseModel):
    amount: int = Field(ge=-100000, le=100000)
    note: str = ""


class GenCodesIn(BaseModel):
    value: int = Field(ge=1, le=100000)
    count: int = Field(ge=1, le=100)


class RedeemCodeOut(BaseModel):
    id: int
    code: str
    value: int
    status: str
    used_by_email: Optional[str]
    used_at: Optional[datetime]
    created_at: datetime


class AdminUserOut(BaseModel):
    id: int
    email: str
    username: str
    role: str
    credits: int
    task_count: int
    created_at: datetime


# ---- Worker 池监控 ----
class WorkerOut(BaseModel):
    url: str
    role: str                    # generate | upscale
    tags: list[str]
    healthy: bool
    busy: bool
    task_id: Optional[int] = None
    consecutive_fails: int


class WorkerPoolOut(BaseModel):
    mock: bool
    workers: list[WorkerOut]
    queued: int                  # 待领取（含增强中）
    generating: int              # 生成阶段（排队+执行）
    upscaling: int               # 超分阶段（排队+执行）

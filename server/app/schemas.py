"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ---- 认证 ----
class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=6, max_length=64)
    # V2 协议：注册页那 1 个合并勾选框覆盖的文档版本（可选，缺省按当前版本留痕）
    agreement_versions: Optional[dict] = None


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
    md5: str = ""
    size: int = 0
    # True 表示命中了库中同槽位同内容的既有素材（未重复入库，直接复用）
    duplicate: bool = False


class UploadListItemOut(BaseModel):
    """资产管理页的上传素材列表项：附带类型与创建时间。"""

    id: int
    slot: str
    filename: str
    url: str
    kind: str        # image | video | audio（按文件后缀判定）
    created_at: datetime
    md5: str = ""    # 内容指纹（历史记录首次列出时补算）
    size: int = 0    # 字节大小
    dup_count: int = 1  # 同内容素材份数（>1 表示有重复）


class DedupGroupOut(BaseModel):
    """一组重复素材（同一 md5）：保留 keep_id，删除 remove_ids。"""

    md5: str
    kind: str
    filename: str
    url: str
    created_at: datetime
    size: int
    count: int              # 该内容总份数（含保留项与被保护的）
    keep_id: int            # 保留的最新一条
    remove_ids: list[int]   # 可安全删除的记录
    removable_bytes: int
    protected_count: int = 0  # 被历史任务引用、不删的重复项数量


class DedupPreviewOut(BaseModel):
    groups: list[DedupGroupOut]
    group_count: int
    removable_count: int
    removable_bytes: int
    protected_count: int = 0


class DedupResultOut(BaseModel):
    removed: int
    freed_bytes: int
    groups: int
    protected_count: int = 0


# ---- 视频任务 ----
class VideoCreateIn(BaseModel):
    mode: Literal["t2v", "flf2v", "r2v", "director"]
    prompt: str = Field(min_length=1, max_length=2000)
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    duration: Literal[5, 8, 10, 15] = 5
    resolution: Literal["768p", "1k", "2k", "4k"] = "768p"
    enhance: bool = True
    scene: Literal["general", "drama", "ecommerce", "music"] = "general"
    first_image_id: Optional[int] = None
    last_image_id: Optional[int] = None
    # 全能参考资料 id：图片/视频/音频混存，总数上限 9（其中视频 ≤3、音频 ≤3）
    ref_image_ids: list[int] = Field(default_factory=list, max_length=9)
    # 长视频导演台（mode=director）段清单，1..64 段；留空则用 prompt+duration 作单段
    segments: list["DirectorSegmentIn"] = Field(default_factory=list, max_length=64)


class DirectorSegmentIn(BaseModel):
    """长视频导演台的单段：一条完整本段提示词 + 本段参考图。

    duration 会被吸附到 H3 合法帧数 5+17n（与插件 minimax_h3_timeline_director.py:783 同规则）。
    """

    prompt: str = Field(min_length=1, max_length=8000)
    duration: float = Field(default=10.0, gt=0, le=10)  # 秒（每镜头上限 10 秒）
    # 本段参考图上限 9（与插件 MAX_REF_IMAGES=9 / r2v 参考上限一致）：
    # 段间连续性素材 = 上一段末帧 + 从上一段均匀抽取的 5 帧，共 6 张
    ref_image_ids: list[int] = Field(default_factory=list, max_length=9)


class AdvideoCreateIn(BaseModel):
    """电商广告片：商品图(+可选参考图) + 一句话场景 → 候选广告图 → 人工确认 → 视频。"""

    prompt: str = Field(min_length=1, max_length=2000)   # 场景描述（喂 qwen21 PE，中文可）
    product_image_ids: list[int] = Field(min_length=1, max_length=3)   # 商品图，第 1 张为主
    ref_image_ids: list[int] = Field(default_factory=list, max_length=6)  # 可选参考图
    video_prompt: str = Field(default="", max_length=2000)   # 视频提示词（独立框；留空=同 prompt）
    aspect_ratio: Literal["16:9", "9:16", "1:1"] = "16:9"
    duration: Literal[5, 8, 10, 15] = 8
    resolution: Literal["768p", "1k", "2k", "4k"] = "768p"
    image_count: int = Field(default=3, ge=1, le=4)          # 默认 3 张候选（用户拍板）
    enhance: bool = True                                     # Content-IR 增强（默认开启）
    # 生视频增强路由：llm=主方案（qwen3.8-flash，默认）/ content_ir=C臂备选 / local=A臂（0 付费）
    video_prompt_mode: Optional[Literal["llm", "content_ir", "local"]] = None
    scene: Literal["general", "drama", "ecommerce", "music"] = "ecommerce"


class AdvideoConfirmIn(BaseModel):
    """人工确认广告图（强制关卡）：选定后写入 chosen_image 并转入视频阶段。"""

    image_index: int = Field(ge=0, le=9)
    video_prompt: str = Field(default="", max_length=2000)
    enhance: Optional[bool] = None
    video_prompt_mode: Optional[Literal["llm", "content_ir", "local"]] = None  # 生视频增强路由：llm=主方案 / content_ir=C臂备选 / local=A臂


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
    # ---- 电商广告片（mode=advideo）----
    stage: str = ""                      # images_queued/images_running/image_ready/video
    ad_image_urls: list[str] = []        # 候选广告图（/files/adimage/<id>?index=N）
    image_prompt: str = ""               # 图像阶段提示词（与视频 prompt 隔离）
    chosen_index: int = -1               # 人工确认选中的序号（-1=未确认）
    chosen_image_url: Optional[str] = None
    ad_image_cost: int = 0               # 广告图阶段已扣积分（独立于 cost）
    first_image_url: Optional[str]
    last_image_url: Optional[str]
    ref_image_urls: list[str]
    ref_video_urls: list[str] = []
    ref_audio_urls: list[str] = []
    parent_task_id: Optional[int] = None
    upscale_target: Optional[str] = None
    worker_url: str = ""
    # 导演台段清单（已吸附的 frames/start_frame/end_frame/seed 原样回传，供核对）
    segments: list[dict] = []
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
    resolution: Literal["1k", "2k", "4k"]


class PackageOut(BaseModel):
    name: str
    credits: int
    price: str
    tag: str
    description: str


class PricingOut(BaseModel):
    signup_bonus: int
    cost_ad_image: int
    cost_768p_5s: int
    cost_768p_8s: int
    cost_768p_10s: int
    cost_768p_15s: int
    cost_1k_extra: int
    cost_2k_extra: int
    cost_4k_extra: int
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
    disabled: bool = False      # 手动置忙（外部清单指定）
    task_id: Optional[int] = None
    consecutive_fails: int
    # 「间隙跑」间隔（秒）：来自该节点标签 gap:<n>（节点固有属性）；非间隙节点为 None
    gap_sec: Optional[float] = None
    # 云端实例（clouds/*.env）；非云节点为 None / false
    platform: Optional[str] = None
    instance_id: Optional[str] = None      # 形如 autodl:pro-7889ca37d10f
    instance_uuid: Optional[str] = None
    instance_status: Optional[str] = None  # running / shutdown / ...
    op_state: Optional[str] = None         # idle | starting | stopping
    power_controllable: bool = False
    # 最近一次手动开关机结果（失败原因持久化；白名单字段，无凭据）
    last_op_action: Optional[str] = None   # on | off | status
    last_op_ok: Optional[bool] = None
    last_op_code: Optional[str] = None
    last_op_msg: Optional[str] = None
    last_op_at: Optional[float] = None


class WorkerPoolOut(BaseModel):
    mock: bool
    workers: list[WorkerOut]
    queued: int                  # 待领取（含增强中）
    generating: int              # 生成阶段（排队+执行）
    upscaling: int               # 超分阶段（排队+执行）


# ---- 云端实例（远程开机/关机）----
class CloudInfoOut(BaseModel):
    cloud_id: str
    platform: str
    instance_id: str
    instance_uuid: str
    worker_url: str
    display_name: str
    instance_status: Optional[str] = None
    op_state: str = "idle"
    worker_healthy: bool = False
    power_controllable: bool = False
    allow_power_off: bool = True
    boot_command: str = ""
    ready_timeout: int = 600
    last_error: str = ""
    last_op: dict = {}
    spec: dict = {}


class CloudPowerIn(BaseModel):
    url: str
    action: Literal["on", "off"]


class CloudPowerOut(BaseModel):
    ok: bool = True
    action: str
    instance: str
    status: str = ""
    instance_status: Optional[str] = None
    eta_s: Optional[int] = None
    already: bool = False
    msg: str = ""
    request_id: str = ""


# ---- 法律文本（用户协议 V2：版本查询 + 留痕，不含拦截） ----
class LegalDocOut(BaseModel):
    key: str
    title: str
    path: str
    version: str


class LegalCurrentOut(BaseModel):
    version: str
    updated: str
    effective: str
    docs: list[LegalDocOut]


class LegalAckIn(BaseModel):
    entry: str = "update_notice"
    versions: Optional[dict] = None

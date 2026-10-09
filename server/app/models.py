"""数据模型：用户 / 上传 / 任务 / 积分流水 / 兑换码。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(60))
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(10), default="user")  # user | admin
    credits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Upload(Base):
    """用户上传的媒体素材（首帧 / 尾帧 / 参考图、参考视频、参考音频）。"""

    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slot: Mapped[str] = mapped_column(String(16))  # first | last | reference
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    # 内容指纹（md5）与字节大小：资产去重的依据，历史记录惰性补算
    md5: Mapped[str] = mapped_column(String(32), default="", index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Task(Base):
    """视频生成任务。状态机：
    queued → enhancing(可选) → generating_768p → upscaling(1k/2k，可选) → done / failed
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    mode: Mapped[str] = mapped_column(String(10))  # t2v | flf2v | r2v | director
    prompt: Mapped[str] = mapped_column(Text)
    enhanced_prompt: Mapped[str] = mapped_column(Text, default="")
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="16:9")
    duration: Mapped[int] = mapped_column(Integer, default=5)
    resolution: Mapped[str] = mapped_column(String(8), default="768p")  # 768p | 2k
    enhance: Mapped[bool] = mapped_column(Boolean, default=True)
    scene: Mapped[str] = mapped_column(String(16), default="general")  # general|drama|ecommerce|music

    first_image_id: Mapped[Optional[int]] = mapped_column(ForeignKey("uploads.id"))
    last_image_id: Mapped[Optional[int]] = mapped_column(ForeignKey("uploads.id"))
    # 全能参考模式的多张参考图（JSON 数组，官方上限 9 张）
    ref_image_ids: Mapped[str] = mapped_column(Text, default="")
    # 长视频导演台（mode=director）段清单：JSON 数组，每项
    # {index, prompt, ref_image_ids, frames, start_frame, end_frame, seed}
    segments: Mapped[str] = mapped_column(Text, default="")

    # ---- 电商广告片（mode=advideo）：先出广告图 → 人工确认 → 再出视频 ----
    # 候选广告图本地路径（JSON 数组，默认 3 张，落在 DATA_DIR/advideo/<task_id>/）
    ad_image_paths: Mapped[str] = mapped_column(Text, default="")
    # 图像阶段提示词（喂 qwen21 PE；与 task.prompt「视频提示词」严格隔离）
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    # 广告图阶段输入素材 upload.id（JSON 数组：第 1 个=商品图，其后=可选参考图）
    ad_input_ids: Mapped[str] = mapped_column(Text, default="")
    # 候选图张数（默认 3）
    ad_image_count: Mapped[int] = mapped_column(Integer, default=3)
    # 商品图张数（ad_input_ids 前 N 项为商品图：第 1 张为主图、其余为细节图）
    ad_product_count: Mapped[int] = mapped_column(Integer, default=0)
    # 用户人工确认选中的广告图（本地路径；非空才允许进入视频阶段）
    chosen_image: Mapped[str] = mapped_column(String(512), default="")
    chosen_index: Mapped[int] = mapped_column(Integer, default=-1)
    # 生视频提示词增强路由（任务级覆盖）：content_ir=C臂（默认）| local=A臂；空=跟随全局默认
    video_prompt_mode: Mapped[str] = mapped_column(String(16), default="")


    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    cost: Mapped[int] = mapped_column(Integer, default=0)
    video_path: Mapped[str] = mapped_column(String(512), default="")
    comfy_prompt_id: Mapped[str] = mapped_column(String(64), default="")
    # 集群调度：失败重投计数与当前占用节点（追溯用）
    # 高清升级：关联原始 768p 任务（post-hoc 超分）
    parent_task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"))
    upscale_target: Mapped[Optional[str]] = mapped_column(String(8))  # 1k | 2k | None

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker_url: Mapped[str] = mapped_column(String(128), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class CreditLog(Base):
    """积分流水（使用台账）。amount 正为入账、负为出账。"""

    __tablename__ = "credit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    # signup | consume | refund | redeem | adjust
    type: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(255), default="")
    task_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tasks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class RedeemCode(Base):
    """充值兑换码：管理后台生成，用户在充值页兑换。"""

    __tablename__ = "redeem_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    value: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="unused")  # unused | used
    used_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AgreementConsent(Base):
    """用户协议同意留痕（V2 体验优先版：**只记录，不拦截**）。

    doc_type: ua(用户协议) | pp(隐私政策) | ai(AI标识说明) | credits(计费规则) | api(API条款)
    entry:    register(注册勾选) | login(登录弱提示) | update_notice(更新横幅关闭)
    """

    __tablename__ = "agreement_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16), default="accept")
    entry: Mapped[str] = mapped_column(String(24), default="register")
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

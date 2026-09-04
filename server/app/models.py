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
    """用户上传的图片（首帧 / 尾帧 / 参考图）。"""

    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slot: Mapped[str] = mapped_column(String(16))  # first | last | reference
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Task(Base):
    """视频生成任务。状态机：
    queued → enhancing(可选) → generating_768p → upscaling(1k/2k，可选) → done / failed
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    mode: Mapped[str] = mapped_column(String(10))  # t2v | flf2v | r2v
    prompt: Mapped[str] = mapped_column(Text)
    enhanced_prompt: Mapped[str] = mapped_column(Text, default="")
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="16:9")
    duration: Mapped[int] = mapped_column(Integer, default=5)
    resolution: Mapped[str] = mapped_column(String(8), default="768p")  # 768p | 2k
    enhance: Mapped[bool] = mapped_column(Boolean, default=True)

    first_image_id: Mapped[Optional[int]] = mapped_column(ForeignKey("uploads.id"))
    last_image_id: Mapped[Optional[int]] = mapped_column(ForeignKey("uploads.id"))
    # 全能参考模式的多张参考图（JSON 数组，官方上限 9 张）
    ref_image_ids: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    cost: Mapped[int] = mapped_column(Integer, default=0)
    video_path: Mapped[str] = mapped_column(String(512), default="")
    comfy_prompt_id: Mapped[str] = mapped_column(String(64), default="")
    # 集群调度：失败重投计数与当前占用节点（追溯用）
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

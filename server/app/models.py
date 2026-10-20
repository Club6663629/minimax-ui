"""数据模型：用户 / 上传 / 任务 / 积分流水 / 兑换码。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # 广告图阶段已扣积分（= cost_ad_image × 张数；独立于 cost，避免与视频阶段 cost>0 跳过逻辑冲突）
    # 提交生成时一次性扣除；出图阶段系统失败全额退还并置 0；删除/重生成不退。
    ad_image_cost: Mapped[int] = mapped_column(Integer, default=0)
    # 生视频提示词增强路由（任务级覆盖）：content_ir=C臂（默认）| local=A臂；空=跟随全局默认
    video_prompt_mode: Mapped[str] = mapped_column(String(16), default="")
    # 自动模式：候选图出完自动确认（选第 N 张）直接进视频阶段，无需人工点击；默认 True（用户拍板 2026-10-10）
    ad_auto_confirm: Mapped[bool] = mapped_column(Boolean, default=True)


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


class DramaProject(Base):
    """短剧项目：一部剧的元信息，内含多集。"""

    __tablename__ = "drama_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    logline: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(32), default="")
    style_bible: Mapped[str] = mapped_column(Text, default="")
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="16:9")
    target_episodes: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DramaEpisode(Base):
    """短剧集：七阶段状态机 + 内容指纹 + 活跃 run 闸门。"""

    __tablename__ = "drama_episodes"
    __table_args__ = (UniqueConstraint("project_id", "episode_no", name="uq_drama_episode_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("drama_projects.id"), index=True)
    episode_no: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(120), default="")
    brief: Mapped[str] = mapped_column(Text, default="")
    beats: Mapped[str] = mapped_column(Text, default="[]")
    script: Mapped[str] = mapped_column(Text, default="")
    # draft | script_ready | cast_ready | shots_ready | rendering | review | done
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    spec_hash: Mapped[str] = mapped_column(String(40), default="")
    final_artifact_id: Mapped[Optional[int]] = mapped_column(Integer)
    # 并发闸门：非空即本集有活跃 run（条件 UPDATE 抢占，见 drama_runner.submit_full）
    active_run_id: Mapped[Optional[int]] = mapped_column(Integer)
    final_duration: Mapped[float] = mapped_column(Float, default=0.0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DramaSegment(Base):
    """短剧段：一次 director 任务 / 一次审片 / 一次返修的单位。

    一段 30–120s、6–12 镜；段内沿用 39 帧交叠，段间用上一段末帧桥接。
    集仍是剧本与交付容器，段只负责生成、审片、版本与回滚。
    """

    __tablename__ = "drama_segments"
    __table_args__ = (UniqueConstraint("episode_id", "segment_no", name="uq_drama_segment_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("drama_episodes.id"), index=True)
    segment_no: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(120), default="")
    target_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    scene_nos: Mapped[str] = mapped_column(Text, default="[]")
    # cut=硬切；continue=承接上一段（需要桥接帧）
    entry_mode: Mapped[str] = mapped_column(String(12), default="cut")
    bridge_upload_id: Mapped[Optional[int]] = mapped_column(Integer)
    bridge_note: Mapped[str] = mapped_column(Text, default="")
    # ready | rendering | review | approved
    status: Mapped[str] = mapped_column(String(16), default="ready")
    spec_hash: Mapped[str] = mapped_column(String(40), default="")
    cut_artifact_id: Mapped[Optional[int]] = mapped_column(Integer)
    active_run_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DramaCharacter(Base):
    """短剧角色（项目级）：跨集复用同一张脸，locked 后 extract 不覆盖。"""

    __tablename__ = "drama_characters"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_drama_character_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("drama_projects.id"), index=True)
    first_episode_id: Mapped[Optional[int]] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(16), default="support")  # main | support | extra
    gender: Mapped[str] = mapped_column(String(16), default="")
    age: Mapped[str] = mapped_column(String(16), default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    costume: Mapped[str] = mapped_column(Text, default="")
    voice: Mapped[str] = mapped_column(String(80), default="")
    card_upload_id: Mapped[Optional[int]] = mapped_column(Integer)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DramaScene(Base):
    """短剧场景（集级）：scene_no 集内唯一，供分镜引用。"""

    __tablename__ = "drama_scenes"
    __table_args__ = (UniqueConstraint("episode_id", "scene_no", name="uq_drama_scene_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("drama_episodes.id"), index=True)
    scene_no: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(80), default="")
    location: Mapped[str] = mapped_column(String(80), default="")
    time_of_day: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    anchor_upload_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DramaShot(Base):
    """短剧镜头：时长 2–10s，四态生命周期，含返修成本追踪。"""

    __tablename__ = "drama_shots"
    __table_args__ = (UniqueConstraint("episode_id", "shot_no", name="uq_drama_shot_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("drama_episodes.id"), index=True)
    segment_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    shot_no: Mapped[int] = mapped_column(Integer, default=1)
    scene_no: Mapped[int] = mapped_column(Integer, default=1)
    beat_id: Mapped[str] = mapped_column(String(16), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=5.0)
    shot_size: Mapped[str] = mapped_column(String(24), default="")
    camera: Mapped[str] = mapped_column(String(24), default="")
    movement: Mapped[str] = mapped_column(String(24), default="")
    character_ids: Mapped[str] = mapped_column(Text, default="[]")
    ref_upload_ids: Mapped[str] = mapped_column(Text, default="[]")
    continuity_note: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    audio_note: Mapped[str] = mapped_column(Text, default="")
    spec_hash: Mapped[str] = mapped_column(String(40), default="")
    # ready | rendered | approved | rejected
    status: Mapped[str] = mapped_column(String(16), default="ready")
    version_no: Mapped[int] = mapped_column(Integer, default=0)
    repair_count: Mapped[int] = mapped_column(Integer, default=0)
    repair_cost_total: Mapped[int] = mapped_column(Integer, default=0)
    qa: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class DramaArtifact(Base):
    """短剧产物：clip / segment / final / master / sheet，version_no=run.id。"""

    __tablename__ = "drama_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("drama_episodes.id"), index=True)
    shot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drama_shots.id"), index=True)
    segment_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)  # clip | segment | final | master | sheet
    version_no: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(512), default="")
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    frames: Mapped[int] = mapped_column(Integer, default=0)
    spec_hash: Mapped[str] = mapped_column(String(40), default="")
    task_id: Mapped[Optional[int]] = mapped_column(Integer)
    qa: Mapped[str] = mapped_column(Text, default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DramaRun(Base):
    """短剧生成批次：segment=分段生成，patch=单镜返修；1:1 对应 tasks。"""

    __tablename__ = "drama_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("drama_episodes.id"), index=True)
    segment_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(8), default="segment")  # segment | patch
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    shot_ids: Mapped[str] = mapped_column(Text, default="[]")
    plan: Mapped[str] = mapped_column(Text, default="")
    spec_hash: Mapped[str] = mapped_column(String(40), default="")
    cost: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


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

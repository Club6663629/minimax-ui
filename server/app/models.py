"""数据模型：用户 / 上传 / 任务 / 积分流水 / 兑换码。"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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


class HibtvProject(Base):
    """短剧工作台项目：一次「定稿剧本 + 美术设定」的导入。

    状态：draft（已建项目，待解析）→ outlining（解析/大纲中）→ ready（可进入制作）
    """

    __tablename__ = "hibtv_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    genre: Mapped[str] = mapped_column(String(64), default="")
    episodes: Mapped[int] = mapped_column(Integer, default=1)
    minutes_min: Mapped[int] = mapped_column(Integer, default=2)
    minutes_max: Mapped[int] = mapped_column(Integer, default=5)
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="9:16")

    script_name: Mapped[str] = mapped_column(String(255), default="")
    script_path: Mapped[str] = mapped_column(String(512), default="")
    script_text: Mapped[str] = mapped_column(Text, default="")
    art_name: Mapped[str] = mapped_column(String(255), default="")
    art_path: Mapped[str] = mapped_column(String(512), default="")
    art_text: Mapped[str] = mapped_column(Text, default="")
    # 必须保留的名场面 / 场次（自由文本）
    keeps: Mapped[str] = mapped_column(Text, default="")
    # 解析汇总：{episodes, scenes, lines, cast, scene, prop, effect}
    detected: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class HibtvDoc(Base):
    """内核文档：outline / cast / art / script / storyboard，data 存 skill schema 的原始 JSON。

    ep=0 表示全集级文档（outline/cast/art），>0 表示分集文档（script/storyboard 按集存）。
    """

    __tablename__ = "hibtv_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("hibtv_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))     # outline|cast|art|script|storyboard
    ep: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[str] = mapped_column(Text, default="{}")
    # 质量门结果：{status: ok|fail|skipped, problems: [...], checked_at, runner}
    gates: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="empty")  # empty|running|done|failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class HibtvAsset(Base):
    """角色 / 场景 / 道具卡：一条资产对应一个锚点图 + N 个派生视图。"""

    __tablename__ = "hibtv_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("hibtv_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)   # cast|scene|prop
    code: Mapped[str] = mapped_column(String(16), default="")  # C01|S01|P01
    name: Mapped[str] = mapped_column(String(120), default="")
    # 卡片内容：提示词 / 音色 / 层级 / 关系 / 原文案
    data: Mapped[str] = mapped_column(Text, default="{}")
    # 视图表：{view: {path, upload_id, version, uploaded, stale, task_id}}
    views: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="empty")  # empty|drawing|review|done
    version: Mapped[int] = mapped_column(Integer, default=0)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class HibtvSegment(Base):
    """分镜段：一段 = 一次 H3 生成（≤ HIBTV_SEG_MAX_SECONDS 秒），可多版本、可回切。"""

    __tablename__ = "hibtv_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("hibtv_projects.id"), index=True)
    ep: Mapped[int] = mapped_column(Integer, default=1, index=True)
    seg_id: Mapped[str] = mapped_column(String(16), default="")   # E01-01
    idx: Mapped[int] = mapped_column(Integer, default=0)
    # 段内容：{id, sceneIndex, seconds, cuts[], h3Prompt, blocking, soundscape, music}
    data: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="none")  # none|running|done|failed
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    # [{v, task_id, path, seconds, at}]
    versions: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class HibtvRender(Base):
    """整集成片：ffmpeg 拼接产物 + cutlist（计划秒 vs 实测秒）。"""

    __tablename__ = "hibtv_renders"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("hibtv_projects.id"), index=True)
    ep: Mapped[int] = mapped_column(Integer, default=1, index=True)
    path: Mapped[str] = mapped_column(String(512), default="")
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    size: Mapped[int] = mapped_column(Integer, default=0)
    cutlist: Mapped[str] = mapped_column(Text, default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class HibtvJob(Base):
    """短剧作业：LLM 生成 / 出图 / 拼接，统一状态机供前端轮询。"""

    __tablename__ = "hibtv_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("hibtv_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))       # outline|cast|art|script|storyboard|images|compose
    ep: Mapped[int] = mapped_column(Integer, default=0)
    ref: Mapped[str] = mapped_column(String(32), default="")  # 资产 code / 段号等
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(120), default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class HibtvChange(Base):
    """变更底账：就地编辑 / 生成覆盖 / 删除，都可撤销。"""

    __tablename__ = "hibtv_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("hibtv_projects.id"), index=True)
    who: Mapped[str] = mapped_column(String(32), default="你")
    what: Mapped[str] = mapped_column(String(255), default="")
    path: Mapped[str] = mapped_column(String(255), default="")
    from_value: Mapped[str] = mapped_column(Text, default="")
    to_value: Mapped[str] = mapped_column(Text, default="")
    reverted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


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


class ApiKey(Base):
    """长期 API Key（平台为租户/数字员工签发，不随 JWT 过期）。

    key_hash: sha256(明文) 十六进制；明文只在签发时返回一次。
    revoked_at 非空即失效；last_used_at 便于审计（每分钟最多回写一次）。
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")
    label: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

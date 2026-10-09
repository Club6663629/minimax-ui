"""全局配置：支持环境变量与 .env 文件覆盖。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- 服务 ----
    jwt_secret: str = "please-change-me-in-production"
    jwt_expire_minutes: int = 7 * 24 * 60
    database_url: str = "sqlite:///./data/app.db"
    data_dir: str = "./data"

    # 启动时自动创建管理员（可选，二者都配置才生效）
    admin_email: str = ""
    admin_password: str = ""
    admin_username: str = "admin"

    # ---- ComfyUI ----
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_poll_interval: float = 3.0
    comfyui_timeout_minutes: int = 30
    # 本机无 ComfyUI 时，用 mock 模式模拟任务全流程（联调前端用）
    mock_comfy: bool = False
    video_fps: int = 25

    # ---- ComfyUI 集群（多卡并发，见《H3集群部署方案》）----
    # 条目间分号分隔，每项 "url|角色|标签"（标签内逗号分隔）；角色 generate|upscale；
    # 标签：heavy(长片段优先)/1k/2k/overflow/unet:<权重文件名>
    # 留空则退回 comfyui_url 单实例串行模式（行为与旧版一致）
    comfyui_workers: str = ""
    # ops 控制密钥（内网运维开关，如 qwen21 模式把节点置忙）；留空=关闭该接口
    ops_control_key: str = ""
    upscale_enabled: bool = True          # 本地超分池开关（关闭后 1K/2K 档不可提交）
    # 长视频导演台（TimelineDirector，director_api.json）后端入口开关；默认关，拍板后置 true
    director_enabled: bool = False
    # ---- 电商广告片 advideo：商品图 → 广告图（人工确认） → 广告视频 ----
    advideo_enabled: bool = True
    # ---- 【临时 2026-10-08 · 用户指令】广告片功能开发中：仅白名单账号可见/可用 ----
    # 一键放开：把 advideo_admin_only 改为 False（或 .env 加 ADVIDEO_ADMIN_ONLY=false）后
    #   systemctl restart minimax-ui-api  即恢复全员可用（前端自动显示入口）。
    advideo_admin_only: bool = True
    # 白名单（逗号分隔，大小写无关）；留空则退化为「role=admin 可见」
    advideo_admin_emails: str = "admin@minimax-studio.com"
    # 广告图阶段专用 qwen21 节点（ComfyUI HTTP 入口，例 http://192.168.10.246:8192）
    advideo_image_worker: str = ""
    advideo_image_width: int = 1920          # 用户拍板 ③：1920x1088
    advideo_image_height: int = 1088
    advideo_image_count: int = 3             # 用户拍板 ②：默认 3 张候选
    advideo_image_timeout_minutes: int = 30  # 单张含 PE 冷启（实测 195-285s）+ 出图 52s
    # A100 qwen21 节点「间隙跑」：每次出图任务完成后间隔 N 秒再发下一张（散热/防 84C 降频）；0=关闭
    advideo_image_gap_sec: float = 5.0
    # ---- 一致性（P0/P1）----
    # 图像阶段 PE 系统提示词：True=「电商商品保真 v1」（487 switch=true）；False=通用增强器（fallback）
    advideo_fidelity_pe: bool = True
    # 画幅：True=latent 走 456 EmptyLatentImage（按前端比例派生，图/片同比例）；False=跟随参考图比例（旧行为）
    advideo_canvas_follow_ratio: bool = True
    # 474 TextEncodeQwenImage21 的 reference 缩放档（0=保持各参考图原始尺寸）
    advideo_pe_resolution: int = 0
    advideo_max_product_images: int = 3      # 商品图上限（主图 + 细节图）
    advideo_max_ref_images: int = 2          # 参考图（人物/场景）上限
    advideo_pe_retries: int = 1              # PE 空输出重试次数（每次约 +54s；1=不重试，直接回退配方）
    advideo_image_prompt_mode: str = "llm"     # 生图提示词来源：llm=提示词增强 agent（默认，qwen3.8-flash，见 advllm.py）/ set=一套图（SAI 直写，锁同一模特/商品/场景只换机位）/ recipe=10-07 配方 / pe=PE 改写
    # ---- LLM 提示词增强 agent（2026-10-09）：advideo_image_prompt_mode=="llm" 时启用 ----
    # 由多模态 flash 模型（关闭思考、支持图像理解）扮演 qwen-image-2.1 提示词改写 agent，
    # 看着商品参考图把用户大白话改写成图像编辑指令；失败/无 key/空输出/未过守门 → 回退本地规则（0 付费）。
    # provider：qwen=千问 qwen3.8-flash（默认）/ deepseek=DeepSeek deepseek-flash；
    #   两者均 OpenAI 兼容 /chat/completions，图片以 base64 image_url 放 user 消息。
    #   对应 provider 的 *_api_key 为空 = 该臂不可用，自动回退本地规则。
    advideo_llm_provider: str = "qwen"           # qwen | deepseek
    advideo_llm_thinking: bool = False           # False → 关思考（qwen: enable_thinking=false / deepseek: thinking.type=disabled）
    advideo_llm_vision_detail: str = "high"      # image_url.detail：low/high/original/auto（主商品图用 high 读清材质/五金/标签）
    advideo_llm_timeout: int = 60
    advideo_llm_temperature: float = 0.4
    advideo_llm_max_tokens: int = 1200
    # provider 专属：key + base + model
    qwen_api_key: str = ""                        # 千问平台/百炼 API Key（QWEN_API_KEY）
    qwen_api_base: str = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    qwen_llm_model: str = "qwen3.8-flash"         # 多模态，可看图（图像理解）
    deepseek_api_key: str = ""                    # DeepSeek API Key（DEEPSEEK_API_KEY）
    deepseek_api_base: str = "https://api.deepseek.com"
    deepseek_llm_model: str = "deepseek-flash"    # 多模态，可看图（图像理解）
    # ---- 提示词增强层（P4，2026-10-08）：用户短句/【场景】【光线】… → 结构化增强提示词；
    # 商品一致性做成「不可被用户文本覆盖」的硬前缀；纯规则实现，0 付费（不调 Content-IR / 任何 LLM）。
    advideo_prompt_enhance: bool = True        # 生图阶段增强（set 模式生效；[RAW] 前缀 = 专家模式，跳过增强）
    advideo_video_prompt_enhance: bool = True  # 生视频阶段增强（本地规则；为 True 时 advideo 不走云端 Content-IR）
    advideo_ir_single_shot: bool = True       # Content-IR 输出后处理（advpostir）：剥 [Shot n]/时间码、切镜改连续运镜、去抖、去重，并把保真主句写进正文（advideo8 实测）  # 生视频阶段增强（本地规则；为 True 时 advideo 不走云端 Content-IR）
    # 生视频提示词增强路由（advideo9，2026-10-08；2026-10-09 默认改为 llm 主方案）
    # llm        = 主方案（默认）：qwen3.8-flash 视频增强 agent（videollm.py，装载官方 H3 skill + 电商保真 system）；
    #              失败 / 无 key / 空输出 / 未过守门 → 自动回退 content_ir（C 臂）
    # content_ir = C 臂（备选）：云端 Content-IR（强制电商 skill：商品保真硬约束）+ advpostir 单镜化后处理（付费）
    # local      = A 臂：本地规则增强（advenhance.enhance_video_prompt，0 付费，行为同改造前）
    advideo_video_prompt_mode: str = "llm"
    # ---- 标准创作流程（general/drama/ecommerce/music）视频增强路由（2026-10-09）----
    # llm        = 主方案（默认）：qwen3.8-flash 视频增强 agent（videollm.py）；失败自动回退 content_ir
    # content_ir = 备选/旧行为：仅走云端 Content-IR（apply_scene + cloud.enhance_prompt）
    # off        = 关闭增强（enhance=True 也不改写，直接用原话）
    video_enhance_mode: str = "llm"
    # 视频增强 agent 调参（复用 qwen provider 凭证：qwen_api_key / qwen_api_base / qwen_llm_model）
    video_llm_temperature: float = 0.5
    video_llm_max_tokens: int = 2400      # H3 结构化正文（三段 + [Shot n]）比生图正文长，给足预算
    video_llm_timeout: int = 90
    video_llm_thinking: bool = False      # 关思考（qwen: enable_thinking=false）
    # 视觉增强：把任务的参考图（首/尾帧、参考区图片、advideo 商品图/确认广告图）一并传给 qwen3.8-flash，
    # 让 agent「看图」再改写（拼贴/多姿态/多机位参考图据此保真、按 H3 montage 规则合成单一主体）。
    video_llm_vision: bool = True         # 关闭则回退纯文本改写（不传图）
    video_llm_max_images: int = 4         # 单次增强最多传入的参考图张数（超出的忽略；detail 复用 advideo_llm_vision_detail）

      # set 模式只喂主商品图 1 张（实测多图输入→N 格拼贴）
    # 超分节点 ComfyUI 的 input 绝对目录（供 VOSR2 L2 缓存节点 source_path 用；可 env COMFYUI_INPUT_DIR 覆盖）
    comfyui_input_dir: str = "/data/ComfyUI/input"

    # ---- 云端实例（远程开机/关机）----
    # clouds/*.env 所在目录（一台实例一个文件）；留空 = <server>/clouds
    clouds_dir: str = ""

    # ---- MiniMax 云端 API（可选；为空则纯本地 768p 出片）----
    minimax_api_key: str = ""
    minimax_api_base: str = "https://api.minimax.cn"

    # ---- 计费（积分），均可通过环境变量覆盖 ----
    signup_bonus: int = 50        # 注册赠送
    cost_ad_image: int = 2        # 电商广告片：每张候选广告图消耗积分（提交生成时按张数一次性扣除，出图阶段系统失败全额退还）
    cost_768p_5s: int = 10        # 768p · 5 秒
    cost_768p_8s: int = 15        # 768p · 8 秒
    cost_768p_10s: int = 20       # 768p · 10 秒
    cost_768p_15s: int = 30       # 768p · 15 秒
    cost_1k_extra: int = 8        # 1K 升级附加（本地超分）
    cost_2k_extra: int = 15       # 2K 升级附加
    cost_4k_extra: int = 30       # 4K 升级附加


    @property
    def cloud_enabled(self) -> bool:
        """配置了 MINIMAX_API_KEY 才启用云端提示词增强（Context-IR）。"""
        return bool(self.minimax_api_key)

    @property
    def llm_provider(self) -> str:
        """归一化后的 LLM provider（qwen | deepseek）。"""
        return (str(self.advideo_llm_provider or "qwen").strip().lower() or "qwen")

    @property
    def advideo_llm_api_key(self) -> str:
        """当前 provider 的 API Key（空则该臂不可用）。"""
        return self.qwen_api_key if self.llm_provider == "qwen" else self.deepseek_api_key

    @property
    def advideo_llm_api_base(self) -> str:
        """当前 provider 的 OpenAI 兼容 base（不含 /chat/completions）。"""
        return self.qwen_api_base if self.llm_provider == "qwen" else self.deepseek_api_base

    @property
    def advideo_llm_model(self) -> str:
        """当前 provider 的模型名。"""
        return self.qwen_llm_model if self.llm_provider == "qwen" else self.deepseek_llm_model

    @property
    def advideo_llm_enabled(self) -> bool:
        """生图 LLM 增强臂是否可用：当前 provider 配了 API Key 且生图提示词模式为 llm。"""
        return bool(self.advideo_llm_api_key) and str(self.advideo_image_prompt_mode).lower() == "llm"

    @property
    def video_llm_enabled(self) -> bool:
        """视频 LLM 增强 agent 是否可用：当前 provider（默认 qwen3.8-flash）配了 API Key。
        与生图 agent 共用凭证；无 key 时 _run_enhance 自动回退 Content-IR。"""
        return bool(self.advideo_llm_api_key)


settings = Settings()

# ---- 广告图/视频画幅表（16 的倍数；按前端选择的比例派生，见《一致性方案 v1》§4）----
ADVIDEO_ASPECT_SIZES: dict = {
    "16:9": (1920, 1088),   # ≈2.1MP（用户拍板 ①）
    "9:16": (1088, 1920),
    "1:1": (1440, 1440),
    "4:3": (1600, 1200),
    "3:4": (1200, 1600),
}


def advideo_size_for(aspect_ratio: str) -> tuple:
    """按前端比例返回广告图画布 (width, height)；未知比例退回 settings 默认值。"""
    key = (aspect_ratio or "").strip()
    if key in ADVIDEO_ASPECT_SIZES:
        return ADVIDEO_ASPECT_SIZES[key]
    return (int(settings.advideo_image_width), int(settings.advideo_image_height))

# ---- 目录规划（与部署方案 8.2/8.4 对应：中间产物与成片分离）----
DATA_DIR = Path(settings.data_dir).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"     # 用户上传的首尾帧 / 参考图
STAGING_DIR = DATA_DIR / "staging"    # 768p 中间产物
OUTPUT_DIR = DATA_DIR / "output"      # 最终成片
ADVIDEO_DIR = DATA_DIR / "advideo"    # 广告片候选广告图
WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows"

for _d in (DATA_DIR, UPLOAD_DIR, STAGING_DIR, OUTPUT_DIR, ADVIDEO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

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
    upscale_enabled: bool = True          # 本地超分池开关（关闭后 1K/2K 档不可提交）
    # 长视频导演台（TimelineDirector，director_api.json）后端入口开关；默认关，拍板后置 true
    director_enabled: bool = False
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


settings = Settings()

# ---- 目录规划（与部署方案 8.2/8.4 对应：中间产物与成片分离）----
DATA_DIR = Path(settings.data_dir).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"     # 用户上传的首尾帧 / 参考图
STAGING_DIR = DATA_DIR / "staging"    # 768p 中间产物
OUTPUT_DIR = DATA_DIR / "output"      # 最终成片
WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows"

for _d in (DATA_DIR, UPLOAD_DIR, STAGING_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

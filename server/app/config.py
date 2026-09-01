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

    # ---- MiniMax 云端 API（可选；为空则纯本地 768p 出片）----
    minimax_api_key: str = ""
    minimax_api_base: str = "https://api.minimax.io"

    # ---- 计费（积分），均可通过环境变量覆盖 ----
    signup_bonus: int = 50        # 注册赠送
    cost_768p_5s: int = 10        # 768p · 5 秒
    cost_768p_10s: int = 20       # 768p · 10 秒
    cost_2k_extra: int = 15       # 2K 升级附加


    @property
    def cloud_enabled(self) -> bool:
        """配置了 MINIMAX_API_KEY 才启用云端增强 / 2K 重生成。"""
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

"""FastAPI 入口：装配路由、启动任务 Worker、自动建表与管理员引导。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User  # noqa: F401  确保建表时包含所有模型
from .routers import admin, auth, credits, files, uploads, videos
from .services import billing  # noqa: F401
from .services.worker import start_worker, stop_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _migrate() -> None:
    """轻量迁移：旧库 tasks 表补 ref_image_ids 列（全能参考多图）。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    insp = sa_inspect(engine)
    if not insp.has_table("tasks"):
        return
    cols = {c["name"] for c in insp.get_columns("tasks")}
    if "ref_image_ids" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN ref_image_ids TEXT DEFAULT ''"))
        logger.info("已为 tasks 表补充 ref_image_ids 列")


def _bootstrap() -> None:
    """建表 + 轻量迁移 + 按环境变量创建初始管理员。"""
    Base.metadata.create_all(bind=engine)
    _migrate()
    if settings.admin_email and settings.admin_password:
        db = SessionLocal()
        try:
            exists = db.query(User).filter(User.email == settings.admin_email.lower()).first()
            if not exists:
                admin_user = User(
                    email=settings.admin_email.lower(),
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                )
                db.add(admin_user)
                db.commit()
                logger.info("已创建初始管理员: %s", settings.admin_email)
        finally:
            db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap()
    start_worker()
    logger.info(
        "服务启动 (mock_comfy=%s, cloud=%s, comfyui=%s)",
        settings.mock_comfy, settings.cloud_enabled, settings.comfyui_url,
    )
    yield
    await stop_worker()


app = FastAPI(title="MiniMax H3 Studio API", lifespan=lifespan)

# 开发期放开跨域（生产由 Nginx 同源反代，此配置无实际影响）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(videos.router)
app.include_router(uploads.router)
app.include_router(files.router)
app.include_router(credits.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "mock_comfy": settings.mock_comfy,
        "cloud_enabled": settings.cloud_enabled,
    }

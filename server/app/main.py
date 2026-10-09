"""FastAPI 入口：装配路由、启动任务 Worker、自动建表与管理员引导。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User  # noqa: F401  确保建表时包含所有模型
from .routers import admin, auth, credits, files, legal, ops, uploads, videos
from .services import billing  # noqa: F401
from .services.pool import pool
from .services.worker import start_worker, stop_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _migrate() -> None:
    """轻量迁移：旧库 tasks 表补列（全能参考多图 / 集群调度字段）。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    insp = sa_inspect(engine)
    if not insp.has_table("tasks"):
        return
    cols = {c["name"] for c in insp.get_columns("tasks")}
    with engine.begin() as conn:
        if "ref_image_ids" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN ref_image_ids TEXT DEFAULT ''"))
            logger.info("已为 tasks 表补充 ref_image_ids 列")
        if "attempts" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN attempts INTEGER DEFAULT 0"))
            logger.info("已为 tasks 表补充 attempts 列")
        if "worker_url" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN worker_url VARCHAR(128) DEFAULT ''"))
            logger.info("已为 tasks 表补充 worker_url 列")
    # 旧版 upscaling_2k 状态并入 upscaling
    with engine.begin() as conn:
        conn.execute(text("UPDATE tasks SET status='upscaling' WHERE status='upscaling_2k'"))
    # v2: 高清升级（post-hoc upscale）
    with engine.begin() as conn:
        if "parent_task_id" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER"))
            logger.info("已为 tasks 表补充 parent_task_id 列")
        if "upscale_target" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN upscale_target VARCHAR(8)"))
            logger.info("已为 tasks 表补充 upscale_target 列")
        if "scene" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN scene VARCHAR(16) DEFAULT 'general'"))
            logger.info("已为 tasks 表补充 scene 列")


def _migrate_uploads() -> None:
    """轻量迁移：uploads 表补 md5 / size 列（资产管理去重所需）。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    insp = sa_inspect(engine)
    if not insp.has_table("uploads"):
        return
    cols = {c["name"] for c in insp.get_columns("uploads")}
    with engine.begin() as conn:
        if "md5" not in cols:
            conn.execute(text("ALTER TABLE uploads ADD COLUMN md5 VARCHAR(32) DEFAULT ''"))
            logger.info("已为 uploads 表补充 md5 列")
        if "size" not in cols:
            conn.execute(text("ALTER TABLE uploads ADD COLUMN size INTEGER DEFAULT 0"))
            logger.info("已为 uploads 表补充 size 列")
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uploads_md5 ON uploads (md5)"))


def _migrate_advideo() -> None:
    """轻量迁移：tasks 表补充电商广告片（advideo）列（幂等，兼容 MySQL/TiDB）。"""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    insp = sa_inspect(engine)
    if not insp.has_table("tasks"):
        return
    cols = {c["name"] for c in insp.get_columns("tasks")}
    ddl = {
        "ad_image_paths": "TEXT",
        "image_prompt": "TEXT",
        "ad_input_ids": "TEXT",
        "ad_image_count": "INTEGER DEFAULT 3",
        "ad_product_count": "INTEGER DEFAULT 0",
        "chosen_image": "VARCHAR(512) DEFAULT ''",
        "chosen_index": "INTEGER DEFAULT -1",
        "video_prompt_mode": "VARCHAR(16) DEFAULT ''",
    }
    with engine.begin() as conn:
        for col, typ in ddl.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {typ}"))
                logger.info("已为 tasks 表补充 %s 列", col)


def _bootstrap() -> None:
    """建表 + 轻量迁移 + 按环境变量创建初始管理员。"""
    Base.metadata.create_all(bind=engine)
    _migrate()
    _migrate_advideo()
    _migrate_uploads()
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
    counts = pool.counts()
    logger.info(
        "服务启动 (mock_comfy=%s, cloud=%s, 生成节点=%d, 超分节点=%d)",
        settings.mock_comfy, settings.cloud_enabled,
        counts["generate_total"], counts["upscale_total"],
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
app.include_router(ops.router)
app.include_router(legal.router)


@app.get("/api/health")
def health():
    counts = pool.counts()
    return {
        "status": "ok",
        "mock_comfy": settings.mock_comfy,
        "cloud_enabled": settings.cloud_enabled,
        "upscale_enabled": settings.upscale_enabled,
        "generate_nodes": f"{counts['generate_healthy']}/{counts['generate_total']}",
        "upscale_nodes": f"{counts['upscale_healthy']}/{counts['upscale_total']}",
    }

"""FastAPI 入口：装配路由、启动任务 Worker、自动建表与管理员引导。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User  # noqa: F401  确保建表时包含所有模型
from .routers import admin, auth, credits, files, hibtv, legal, ops, uploads, videos
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
        "ad_image_cost": "INTEGER DEFAULT 0",
        "video_prompt_mode": "VARCHAR(16) DEFAULT ''",
        "ad_auto_confirm": "TINYINT(1) DEFAULT 1",
        # H3 段帧数：5+17n（24fps）。短剧段长按帧算，才装得下 12.9583s（311 帧）
        "frames": "INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        for col, typ in ddl.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {typ}"))
                logger.info("已为 tasks 表补充 %s 列", col)


def _migrate_hibtv() -> None:
    """轻量迁移：短剧表补「小说正文 / codex 产物溯源 / 资产主图」列（幂等）。

    - `hibtv_projects`：novel_*（存量把 `script_text` 拷过去当 legacy 小说）
    - `hibtv_docs`：meta（stage / codex_job_id / sha256 / 产物清单）
    - `hibtv_assets`：main_path / main_upload_id / main_meta（单图直出；`views` 只读兼容）
    - `hibtv_jobs`：codex_job_id / artifacts / usage
    `hibtv_frames` 是新表，由 `Base.metadata.create_all` 建。
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    insp = sa_inspect(engine)
    ddl = {
        "hibtv_projects": {
            "novel_name": "VARCHAR(255) DEFAULT ''",
            "novel_path": "VARCHAR(512) DEFAULT ''",
            "novel_text": "TEXT",
        },
        "hibtv_docs": {"meta": "TEXT"},
        "hibtv_assets": {
            "main_path": "VARCHAR(512) DEFAULT ''",
            "main_upload_id": "INTEGER",
            "main_meta": "TEXT",
        },
        "hibtv_jobs": {
            "codex_job_id": "VARCHAR(64) DEFAULT ''",
            "artifacts": "TEXT",
            "usage": "TEXT",
        },
    }
    with engine.begin() as conn:
        for table, cols in ddl.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col, typ in cols.items():
                if col in have:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typ}"))
                logger.info("已为 %s 表补充 %s 列", table, col)
    # 存量项目：老「定稿剧本」当小说正文（文本阶段需重跑，像素/成片不受影响）
    with engine.begin() as conn:
        if insp.has_table("hibtv_projects"):
            conn.execute(text(
                "UPDATE hibtv_projects SET novel_text = script_text "
                "WHERE (novel_text IS NULL OR novel_text = '') "
                "AND script_text IS NOT NULL AND script_text <> ''"))


def _bootstrap() -> None:
    """建表 + 轻量迁移 + 按环境变量创建初始管理员。"""
    Base.metadata.create_all(bind=engine)
    _migrate()
    _migrate_advideo()
    _migrate_uploads()
    _migrate_hibtv()
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


def _recover_hibtv_jobs() -> None:
    """启动时收敛上一轮进程遗留的 running 短剧作业（此时必然无协程在跑）。"""
    if not getattr(settings, "hibtv_enabled", False):
        return
    from .services import hibtv_jobs

    db = SessionLocal()
    try:
        n = hibtv_jobs.recover_running_jobs(db, only_stale=False)
        if n:
            logger.warning("启动回收 %d 条中断的短剧作业", n)
    except Exception:  # noqa: BLE001 —— 迁就启动，不让恢复失败阻断服务
        logger.exception("短剧作业启动回收失败")
    finally:
        db.close()


async def _reconcile_codex_jobs() -> None:
    """启动自愈：网关重启会把 queued 重新入队（可能重复烧 token）。

    对比 `/v1/jobs?status=queued|running` 与本地已知 jid 表，把不认识的 job 全部 cancel。
    网关不可达时只记日志：那是红态问题，不是启动阻塞问题。
    """
    if not getattr(settings, "hibtv_enabled", False):
        return
    from .services import hibtv_codex

    db = SessionLocal()
    try:
        n = await hibtv_codex.reconcile_orphans(db)
        if n:
            logger.warning("启动取消 %d 条不认识的网关作业（防重复烧 token）", n)
    except Exception:  # noqa: BLE001
        logger.exception("网关作业自愈失败（不影响启动）")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _bootstrap()
    _recover_hibtv_jobs()
    await _reconcile_codex_jobs()
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
app.include_router(hibtv.router)


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

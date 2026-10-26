"""FastAPI 入口：装配路由、启动任务 Worker、自动建表与管理员引导。"""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import hash_password
from .config import settings
from .database import Base, SessionLocal, engine
from .models import User  # noqa: F401  确保建表时包含所有模型
from .routers import admin, auth, credits, drama, files, legal, ops, uploads, videos
from .services import billing  # noqa: F401
from .services import drama_runner
from .services.pool import pool
from .services.worker import start_worker, stop_worker
from .studio import models as studio_models  # noqa: F401  建表时包含 studio_* 表
from .studio import router as studio_router

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
    }
    with engine.begin() as conn:
        for col, typ in ddl.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} {typ}"))
                logger.info("已为 tasks 表补充 %s 列", col)


def _migrate_drama() -> None:
    """轻量迁移：分段生成（drama_segments）+ 旧集回填为单段。

    旧集整集产物原样保留：把旧 full run / final / clip 挂到唯一段上，
    按新口径重算指纹并回写到历史产物，避免升级后成片被误判 stale。
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    from .models import (
        DramaArtifact, DramaEpisode, DramaRun, DramaSegment, DramaShot,
    )
    from .services import drama_composer as composer

    insp = sa_inspect(engine)
    if not insp.has_table("drama_episodes"):
        return

    def _columns(table: str) -> set:
        return {c["name"] for c in insp.get_columns(table)}

    with engine.begin() as conn:
        cols = _columns("drama_shots")
        if "segment_id" not in cols:
            conn.execute(text("ALTER TABLE drama_shots ADD COLUMN segment_id INTEGER"))
            logger.info("已为 drama_shots 表补充 segment_id 列")
        cols = _columns("drama_runs")
        if "segment_id" not in cols:
            conn.execute(text("ALTER TABLE drama_runs ADD COLUMN segment_id INTEGER"))
            logger.info("已为 drama_runs 表补充 segment_id 列")
        cols = _columns("drama_artifacts")
        if "segment_id" not in cols:
            conn.execute(text("ALTER TABLE drama_artifacts ADD COLUMN segment_id INTEGER"))
            logger.info("已为 drama_artifacts 表补充 segment_id 列")

    db = SessionLocal()
    try:
        for episode in db.query(DramaEpisode).all():
            # 已有段的集不再动（否则每次重启都会把用户在编辑后的 stale 产物写成 fresh）
            if db.query(DramaSegment).filter(DramaSegment.episode_id == episode.id).count():
                continue
            shots = (
                db.query(DramaShot).filter(DramaShot.episode_id == episode.id)
                .order_by(DramaShot.shot_no).all()
            )
            if not shots:
                continue
            # 只回填「升级前真正生成过」的集；新集保持未分段，由用户显式分段
            has_history = bool(
                db.query(DramaArtifact.id).filter(
                    DramaArtifact.episode_id == episode.id).first()
            )
            if not has_history:
                continue
            segment = DramaSegment(
                episode_id=episode.id,
                segment_no=1,
                title=f"第 1 段 · {episode.title or '整集'}",
                target_seconds=round(sum(s.duration_s for s in shots), 2),
                scene_nos=json.dumps(sorted({s.scene_no for s in shots})),
                entry_mode="cut",
                status="ready",
            )
            db.add(segment)
            db.flush()
            for shot in shots:
                shot.segment_id = segment.id
            for run in db.query(DramaRun).filter(
                DramaRun.episode_id == episode.id, DramaRun.segment_id.is_(None),
            ).all():
                run.segment_id = segment.id
                if run.mode == "full":
                    run.mode = "segment"
            artifacts = db.query(DramaArtifact).filter(
                DramaArtifact.episode_id == episode.id,
            ).all()
            for artifact in artifacts:
                if artifact.segment_id is None:
                    artifact.segment_id = segment.id

            # 按新口径重算指纹（单段集的集指纹 == 段指纹）
            composer.refresh_episode_spec(db, episode)
            for artifact in artifacts:
                if artifact.kind in ("clip", "segment", "final", "master", "sheet"):
                    artifact.spec_hash = segment.spec_hash
            for run in db.query(DramaRun).filter(DramaRun.episode_id == episode.id).all():
                run.spec_hash = segment.spec_hash

            # 旧 final 复制为段成片，升级后可直接审/交付，不触发重渲染
            final = next((a for a in artifacts if a.kind == "final" and a.is_current), None)
            if final is not None:
                segment.cut_artifact_id = final.id
                segment.status = "review"
                has_segment_artifact = any(
                    a.kind == "segment" and a.segment_id == segment.id for a in artifacts
                )
                if not has_segment_artifact:
                    db.add(DramaArtifact(
                        episode_id=episode.id, shot_id=None, segment_id=segment.id,
                        kind="segment", version_no=final.version_no, path=final.path,
                        duration=final.duration, frames=final.frames,
                        spec_hash=segment.spec_hash, task_id=final.task_id,
                        is_current=True,
                    ))
            if episode.status == "rendering":
                episode.status = "shots_ready"
                episode.active_run_id = None
        db.commit()
    finally:
        db.close()


def _bootstrap() -> None:
    """建表 + 轻量迁移 + 按环境变量创建初始管理员。"""
    Base.metadata.create_all(bind=engine)
    _migrate()
    _migrate_advideo()
    _migrate_uploads()
    _migrate_drama()
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
    sweep_task = None
    if settings.drama_enabled:
        import asyncio
        sweep_task = asyncio.get_event_loop().create_task(drama_runner.sweep_loop())
        logger.info("短剧巡扫已启动（%ss 间隔）", drama_runner.SWEEP_INTERVAL_SECONDS)
    counts = pool.counts()
    logger.info(
        "服务启动 (mock_comfy=%s, cloud=%s, 生成节点=%d, 超分节点=%d)",
        settings.mock_comfy, settings.cloud_enabled,
        counts["generate_total"], counts["upscale_total"],
    )
    yield
    if sweep_task is not None:
        sweep_task.cancel()
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
app.include_router(drama.router)
app.include_router(studio_router.router)


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

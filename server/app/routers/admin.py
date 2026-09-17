"""管理后台路由：用户管理 / 兑换码 / 全站任务 / 用量统计。"""
import logging
import secrets
import subprocess
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import get_admin
from ..database import get_db
from ..models import CreditLog, RedeemCode, Task, User
from ..schemas import (
    AdminAdjustIn, AdminUserOut, CloudInfoOut, CloudPowerIn, CloudPowerOut,
    GenCodesIn, RedeemCodeOut, WorkerOut, WorkerPoolOut,
)
from ..services.billing import add_credits
from ..services.clouds import registry
from ..services.clouds.base import CloudAPIError
from ..services.pool import pool
from .serialize import serialize_task

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = logging.getLogger(__name__)


# ---- 用户管理 ----
@router.get("/users", response_model=list[AdminUserOut])
def list_users(db: Session = Depends(get_db), _admin: User = Depends(get_admin)):
    rows = (
        db.query(
            User,
            func.count(Task.id).label("task_count"),
        )
        .outerjoin(Task, Task.user_id == User.id)
        .group_by(User.id)
        .order_by(User.id)
        .all()
    )
    return [
        AdminUserOut(
            id=u.id, email=u.email, username=u.username, role=u.role,
            credits=u.credits, task_count=cnt, created_at=u.created_at,
        )
        for u, cnt in rows
    ]


@router.post("/users/{user_id}/adjust")
def adjust_credits(
    user_id: int,
    body: AdminAdjustIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin),
):
    if body.amount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "调整额度不能为 0")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if user.credits + body.amount < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "扣减后余额不能为负")
    note = body.note or ("管理员充值" if body.amount > 0 else "管理员扣减")
    add_credits(db, user, body.amount, "adjust", note=f"{note}（操作人 {admin.email}）")
    db.commit()
    return {"credits": user.credits}


# ---- 兑换码 ----
@router.post("/redeem-codes", response_model=list[RedeemCodeOut])
def generate_codes(
    body: GenCodesIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin),
):
    created: list[RedeemCode] = []
    for _ in range(body.count):
        # 12 位大写码，形如 H3AB-CDEF-GHIJ
        raw = secrets.token_hex(6).upper()
        code = f"H3{raw[:4]}-{raw[4:8]}-{raw[8:12]}"
        item = RedeemCode(code=code, value=body.value)
        db.add(item)
        created.append(item)
    db.commit()
    for item in created:
        db.refresh(item)
    return [_code_out(c, db) for c in created]


@router.get("/redeem-codes", response_model=list[RedeemCodeOut])
def list_codes(
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin),
):
    codes = db.query(RedeemCode).order_by(RedeemCode.id.desc()).limit(limit).all()
    return [_code_out(c, db) for c in codes]


def _code_out(code: RedeemCode, db: Session) -> RedeemCodeOut:
    used_by_email = None
    if code.used_by:
        used_user = db.get(User, code.used_by)
        used_by_email = used_user.email if used_user else None
    return RedeemCodeOut(
        id=code.id, code=code.code, value=code.value, status=code.status,
        used_by_email=used_by_email, used_at=code.used_at, created_at=code.created_at,
    )


# ---- 全站任务 ----
@router.get("/tasks")
def list_all_tasks(
    limit: int = Query(default=100, le=500),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin),
):
    q = db.query(Task).order_by(Task.id.desc())
    if status_filter:
        q = q.filter(Task.status == status_filter)
    tasks = q.limit(limit).all()
    emails = {
        u.id: u.email
        for u in db.query(User).filter(User.id.in_([t.user_id for t in tasks])).all()
    } if tasks else {}
    return [serialize_task(t, user_email=emails.get(t.user_id), db=db) for t in tasks]


# ---- Worker 池监控 ----
@router.get("/workers", response_model=WorkerPoolOut)
def worker_pool(db: Session = Depends(get_db), _admin: User = Depends(get_admin)):
    queued = db.query(Task).filter(Task.status.in_(("queued", "enhancing"))).count()
    generating = db.query(Task).filter(Task.status == "generating_768p").count()
    upscaling = db.query(Task).filter(Task.status == "upscaling").count()
    return WorkerPoolOut(
        mock=pool.mock,
        workers=[WorkerOut(**w) for w in pool.snapshot()],
        queued=queued,
        generating=generating,
        upscaling=upscaling,
    )


# ---- 用量统计 ----
@router.get("/stats")
def stats(db: Session = Depends(get_db), _admin: User = Depends(get_admin)):
    user_count = db.query(func.count(User.id)).scalar() or 0
    task_count = db.query(func.count(Task.id)).scalar() or 0
    status_rows = db.query(Task.status, func.count(Task.id)).group_by(Task.status).all()
    status_counts = {s: c for s, c in status_rows}
    consumed = (
        db.query(func.coalesce(func.sum(-CreditLog.amount), 0))
        .filter(CreditLog.type == "consume")
        .scalar()
    )
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_tasks = (
        db.query(func.count(Task.id)).filter(Task.created_at >= today_start).scalar() or 0
    )
    return {
        "user_count": user_count,
        "task_count": task_count,
        "today_tasks": today_tasks,
        "queued": status_counts.get("queued", 0),
        "running": sum(
            status_counts.get(s, 0)
            for s in ("enhancing", "generating_768p", "upscaling")
        ),
        "done": status_counts.get("done", 0),
        "failed": status_counts.get("failed", 0),
        "credits_consumed": int(consumed),
    }


# ---- 云端实例（远程开机 / 关机）----
@router.get("/clouds", response_model=list[CloudInfoOut])
async def list_clouds(_admin: User = Depends(get_admin)):
    """云实例只读信息（状态 / 规格）。绝不返回 token 等凭据。"""
    out: list[CloudInfoOut] = []
    for inst in registry.all_instances():
        await registry.refresh_status(inst)
        await registry.refresh_spec(inst)
        out.append(CloudInfoOut(**inst.to_dict()))
    return out


def _tunnel(action: str) -> None:
    """开关机成功后联动云端 SSH 隧道（方案A，2026-09-16）。

    背景：实例关机期间 autossh 会持续重连（实测 8 小时 98 次 starting ssh，
    全部是 Connection refused），且开机后要等下一个 poll 拍才恢复（实测白等 2.9 分钟）。
    所以：开机成功后主动 start，关机成功后主动 stop，避免关机期无意义重连。

    硬约束：绝不影响开关机 API 返回——任何异常只记日志，不抛出。
    """
    unit = "comfyui-cloud-tunnel"
    verb = "start" if action == "on" else "stop"
    try:
        proc = subprocess.run(
            ["systemctl", verb, unit],
            timeout=10, capture_output=True, text=True,
        )
        if verb == "stop" and proc.returncode == 0:
            # autossh 收到 SIGTERM 会以非 0 退出，systemd 会把 unit 记成 failed（监控里看着像故障），清掉残留状态
            subprocess.run(["systemctl", "reset-failed", unit],
                           timeout=10, capture_output=True, text=True)
        if proc.returncode == 0:
            logger.info("隧道 %s 已下发（unit=%s）", verb, unit)
        else:
            logger.warning("隧道 %s 失败 rc=%s err=%s", verb, proc.returncode,
                           (proc.stderr or "").strip())
    except Exception as exc:  # noqa: BLE001 - 隧道失败不影响开关机结果
        logger.warning("隧道 %s 异常：%r", verb, exc)


@router.post("/clouds/power", response_model=CloudPowerOut)
async def cloud_power(body: CloudPowerIn, _admin: User = Depends(get_admin)):
    """手动开 / 关机。

    范围（用户 2026-09-15 明确）：点按钮直接执行——**不做任务检测拦截、不做中断任务处理**；
    开机后"开机即 worker"由池心跳自愈完成（不改派单逻辑）。

    - 只接受节点 url，实例 uuid 由后端按 clouds/*.env 自查（杜绝操作配置外的实例）
    - 平台失败原样透传 msg + request_id（无卡 / 授权失败 / 实例不存在都能看到原因）
    """
    node = pool.get_node_by_url(body.url)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "未知节点 url")
    inst = getattr(node, "cloud", None)
    if inst is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error_code": "NOT_CLOUD",
                    "msg": "该节点不是云实例（clouds/ 未配置，或 CLOUD_WORKER_URL 与池中 url 不一致）"},
        )
    if not inst.controllable:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error_code": "NO_CREDENTIAL",
                    "msg": "云实例缺少 CLOUD_API_TOKEN / CLOUD_INSTANCE_UUID"},
        )
    try:
        result = await (registry.power_on(inst) if body.action == "on" else registry.power_off(inst))
    except CloudAPIError as exc:
        raise HTTPException(exc.http_status, detail=exc.detail(inst.id))
    # 方案A：开关机成功后联动隧道（失败仅记日志，不影响本接口返回）
    _tunnel(body.action)
    return CloudPowerOut(
        ok=True,
        action=body.action,
        instance=inst.id,
        status=str(result.get("status") or ""),
        instance_status=result.get("instance_status"),
        eta_s=result.get("eta_s"),
        already=bool(result.get("already")),
        msg=("开机指令已下发，云端启动中（就绪后自动接单）" if body.action == "on"
             else "关机指令已下发，节点约 30s 内自动摘牌"),
    )

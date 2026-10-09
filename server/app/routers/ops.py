"""ops 运维控制接口（仅内网 + 静态密钥）：

外部（如 246 的 qwen21 切换脚本）可在后端运行期，把某个 ComfyUI 节点「置忙」，
使其不再被派发新任务（qwen21 模式占用该卡时使用），回切后解除。
认证：请求头 X-Ops-Key 必须等于 settings.ops_control_key（.env 配置；留空=接口关闭）。
默认未配置密钥时返回 503，接口不可用，安全。
"""
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel

from ..config import settings
from ..services.pool import pool

router = APIRouter(prefix="/api/ops", tags=["ops"])
logger = logging.getLogger(__name__)


class DisabledIn(BaseModel):
    urls: list[str] = []
    note: str = ""


def _check(key: str) -> None:
    expect = (settings.ops_control_key or "").strip()
    if not expect:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ops 控制未启用（未配 OPS_CONTROL_KEY）")
    if not secrets.compare_digest((key or "").strip(), expect):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid ops key")


@router.get("/workers/disabled")
def get_disabled(x_ops_key: str = Header(default="")):
    _check(x_ops_key)
    return pool.disabled_state()


@router.put("/workers/disabled")
def set_disabled(body: DisabledIn, x_ops_key: str = Header(default="")):
    _check(x_ops_key)
    logger.info("ops 置忙请求: urls=%s note=%s", body.urls, body.note)
    return pool.set_disabled(body.urls, body.note)


@router.get("/health")
def ops_health(x_ops_key: str = Header(default="")):
    _check(x_ops_key)
    return {"ok": True, "disabled": pool.disabled_state()}

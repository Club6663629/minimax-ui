"""clouds/*.env 注册表：配置加载 / Worker 节点绑定 / 状态刷新 / 开关机编排。

- 目录默认 `server/clouds/`，可用主 .env 的 CLOUDS_DIR 覆盖（部署可指向持久盘）
- 目录不存在 = 无云节点 → 行为与单机部署完全一致（51/246/205 不渲染开关机按钮）
- 实例与节点的映射键是 CLOUD_WORKER_URL（必须与 COMFYUI_WORKERS 里的 url 严格一致），
  因此接口只接受节点 url，杜绝"传任意 uuid 去关别人机器"
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel

from ...config import settings
from .autodl import AutoDLProvider
from .base import CloudAPIError, CloudInstance, CloudProvider

logger = logging.getLogger(__name__)

DEFAULT_CLOUDS_DIR = Path(__file__).resolve().parents[3] / "clouds"

_PROVIDERS: Dict[str, CloudProvider] = {"autodl": AutoDLProvider()}

_instances: List[CloudInstance] = []
_by_url: Dict[str, CloudInstance] = {}
_loaded_dir: Optional[str] = None


class CloudEnvConfig(BaseModel):
    """clouds/<platform>-<uuid>.env 字段定义（见 clouds/example.env）。"""

    CLOUD_ID: str = ""
    CLOUD_PLATFORM: str = "autodl"
    CLOUD_INSTANCE_UUID: str
    CLOUD_WORKER_URL: str
    CLOUD_API_BASE: str = ""
    CLOUD_API_TOKEN: str = ""
    CLOUD_DISPLAY_NAME: str = ""
    CLOUD_BOOT_COMMAND: str = ""
    CLOUD_READY_TIMEOUT: int = 600
    CLOUD_READY_POLL: int = 10
    CLOUD_ALLOW_POWER_OFF: bool = True


def parse_env_file(path: Path) -> Dict[str, str]:
    """极简 KEY=VALUE 解析（支持 # 注释 / 引号 / 行内不做插值）。"""
    values: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def clouds_dir() -> Path:
    return Path(settings.clouds_dir).resolve() if settings.clouds_dir else DEFAULT_CLOUDS_DIR


def load_clouds(force: bool = False) -> List[CloudInstance]:
    """扫描 clouds/*.env → CloudInstance（跳过 example/template；坏配置只告警不中断）。"""
    global _instances, _by_url, _loaded_dir
    directory = clouds_dir()
    if not force and _loaded_dir == str(directory) and _instances:
        return _instances
    _instances = []
    _by_url = {}
    _loaded_dir = str(directory)
    if not directory.is_dir():
        logger.info("云端节点目录不存在（%s）：无云端节点，行为与单机部署一致", directory)
        return _instances
    for path in sorted(directory.glob("*.env")):
        if path.name in ("example.env", "template.env"):
            continue
        try:
            cfg = CloudEnvConfig(**parse_env_file(path))
        except Exception as exc:
            logger.error("云实例配置解析失败（已忽略）: %s: %s", path.name, exc)
            continue
        inst = CloudInstance(
            id=cfg.CLOUD_ID or f"{cfg.CLOUD_PLATFORM}:{cfg.CLOUD_INSTANCE_UUID}",
            platform=cfg.CLOUD_PLATFORM,
            instance_uuid=cfg.CLOUD_INSTANCE_UUID,
            worker_url=cfg.CLOUD_WORKER_URL.rstrip("/"),
            api_base=cfg.CLOUD_API_BASE,
            api_token=cfg.CLOUD_API_TOKEN,
            display_name=cfg.CLOUD_DISPLAY_NAME,
            boot_command=cfg.CLOUD_BOOT_COMMAND,
            ready_timeout=cfg.CLOUD_READY_TIMEOUT,
            ready_poll=cfg.CLOUD_READY_POLL,
            allow_power_off=cfg.CLOUD_ALLOW_POWER_OFF,
            env_file=path.name,
        )
        _instances.append(inst)
        _by_url[inst.worker_url] = inst
        logger.info("已加载云实例: %s（worker=%s, platform=%s, 有凭据=%s）",
                    inst.id, inst.worker_url, inst.platform, inst.controllable)
    return _instances


def all_instances() -> List[CloudInstance]:
    if not _instances:
        load_clouds()
    return _instances


def get_by_worker_url(url: str) -> Optional[CloudInstance]:
    all_instances()
    return _by_url.get((url or "").rstrip("/"))


def provider(inst: CloudInstance) -> CloudProvider:
    impl = _PROVIDERS.get(inst.platform)
    if impl is None:
        raise CloudAPIError(f"未支持的云平台：{inst.platform}", "PLATFORM_UNSUPPORTED", http_status=400)
    return impl


def bind_nodes(nodes) -> None:
    """按 CLOUD_WORKER_URL 把云实例绑到 WorkerNode；对不上只告警（防误操作）。"""
    insts = all_instances()
    if not insts:
        return
    matched = set()
    for node in nodes:
        inst = _by_url.get(node.url.rstrip("/"))
        if inst is not None:
            inst.node = node
            node.cloud = inst
            matched.add(inst.id)
    for inst in insts:
        if inst.id not in matched:
            logger.warning("云实例 %s 的 CLOUD_WORKER_URL=%s 在 COMFYUI_WORKERS 中无匹配节点：不绑定（不可操作）",
                           inst.id, inst.worker_url)


def _mark_op(inst: CloudInstance, action: str, ok: bool, code: str = "",
             msg: str = "", request_id: str = "") -> None:
    """记录最近一次手动开关机结果（白名单字段，绝不含凭据；前端失败原因兜底来源）。"""
    inst.last_op = {"action": action, "ok": ok, "code": code, "msg": msg,
                    "request_id": request_id, "at": time.time()}


async def refresh_status(inst: CloudInstance, force: bool = False,
                         raise_on_error: bool = False) -> Optional[str]:
    """按 TTL 刷新实例状态：过渡期(op!=idle) 5s、平时 60s。"""
    age = time.time() - inst.last_status_at
    ttl = 5 if inst.op_state != "idle" else 60
    if not force and inst.instance_status is not None and age < ttl:
        return inst.instance_status
    try:
        status = await provider(inst).status(inst)
        inst.instance_status = status
        inst.last_status_at = time.time()
        inst.last_error = ""
        if inst.op_state == "starting" and status == "running" and inst.worker_healthy:
            inst.op_state = "idle"
            logger.info("云实例已就绪（节点回池）: %s", inst.id)
        elif inst.op_state == "stopping" and status in ("shutdown", "shutting_down", "stopped"):
            inst.op_state = "idle"
    except CloudAPIError as exc:
        inst.last_error = exc.msg
        # 记入 last_op（action=status）供 /api/admin/clouds 诊断；
        # 但绝不覆盖 1h 内的手动开关机失败记录（否则失败原因会被轮询冲掉）
        prev = inst.last_op or {}
        keep = (prev.get("ok") is False and prev.get("action") in ("on", "off")
                and time.time() - float(prev.get("at") or 0) < 3600)
        if not keep:
            _mark_op(inst, "status", False, exc.code, exc.msg, exc.request_id)
        logger.warning("云实例状态查询失败: %s: %s（request_id=%s）", inst.id, exc.msg, exc.request_id)
        if raise_on_error:
            raise
    return inst.instance_status


async def refresh_spec(inst: CloudInstance, ttl: int = 3600) -> None:
    """规格/区域/价格（只读，供 UI 展示；白名单字段，绝不含凭据）。"""
    if inst.spec and time.time() - inst.last_spec_at < ttl:
        return
    try:
        inst.spec = await provider(inst).snapshot(inst)
        inst.last_spec_at = time.time()
    except CloudAPIError as exc:
        logger.warning("云实例规格查询失败: %s: %s", inst.id, exc.msg)


async def status_loop(interval: float = 15.0) -> None:
    """后台状态轮询（无云实例时立即返回，不占资源）。"""
    if not all_instances():
        return
    logger.info("云实例状态轮询已启动：%d 个实例，间隔 %.0fs", len(all_instances()), interval)
    while True:
        for inst in all_instances():
            try:
                await refresh_status(inst)
            except Exception:  # 轮询循环永不退出
                logger.exception("云实例状态轮询异常: %s", inst.id)
        await asyncio.sleep(interval)


async def power_on(inst: CloudInstance) -> dict:
    """下发开机（payload=gpu + start_command）；开机即 worker 由池心跳自动完成。

    失败（如平台无卡）先写 inst.last_op 再抛出：原因不因前端刷新而丢失。
    """
    async with inst.lock:
        try:
            status = await refresh_status(inst, force=True)
            if status == "running":
                _mark_op(inst, "on", True)
                return {"ok": True, "action": "on", "instance_status": status,
                        "status": "running", "already": True}
            await provider(inst).power_on(inst, inst.boot_command or None)
            inst.op_state = "starting"
            inst.last_op_at = time.time()
            inst.last_error = ""
            _mark_op(inst, "on", True)
            logger.info("云实例开机已下发: %s（start_command=%s）",
                        inst.id, "已配置" if inst.boot_command else "无")
            return {"ok": True, "action": "on", "instance_status": "starting",
                    "status": "starting", "eta_s": inst.ready_timeout}
        except CloudAPIError as exc:
            _mark_op(inst, "on", False, exc.code, exc.msg, exc.request_id)
            logger.warning("云实例开机失败: %s: %s（error_code=%s request_id=%s）",
                           inst.id, exc.msg, exc.code, exc.request_id)
            raise


async def power_off(inst: CloudInstance) -> dict:
    """下发关机；平台失败时抛 CloudAPIError（含平台原文 + request_id）。"""
    async with inst.lock:
        try:
            status = await refresh_status(inst, force=True, raise_on_error=True)
            if status in ("shutdown", "shutting_down", "stopped"):
                inst.op_state = "idle"
                _mark_op(inst, "off", True)
                return {"ok": True, "action": "off", "instance_status": status,
                        "status": "already_off", "already": True}
            await provider(inst).power_off(inst)
            inst.op_state = "stopping"
            inst.last_op_at = time.time()
            inst.last_error = ""
            _mark_op(inst, "off", True)
            logger.info("云实例关机已下发: %s", inst.id)
            return {"ok": True, "action": "off", "instance_status": "shutting_down",
                    "status": "shutting_down"}
        except CloudAPIError as exc:
            _mark_op(inst, "off", False, exc.code, exc.msg, exc.request_id)
            logger.warning("云实例关机失败: %s: %s（error_code=%s request_id=%s）",
                           inst.id, exc.msg, exc.code, exc.request_id)
            raise

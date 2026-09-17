"""云实例模型与 Provider 抽象（多平台可扩：autodl / ali / tencent / volcano ...）。

设计依据《autodl-开关机-方案V2-20260915.md》：
- 一台云端实例 = 一个 clouds/<platform>-<uuid>.env（独立 env，主 .env 完全不动）
- Worker 池按 CLOUD_WORKER_URL 把实例绑定到节点；url 对不上只告警、不生效
- 凭据仅在后端内存使用：不回传前端、不打日志（日志只打 instance id / request_id）
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class CloudAPIError(Exception):
    """云平台 API 失败（code != Success / 网络异常 / 超时）。

    平台原文 msg + request_id 透传给调用方，前端直显，不翻译不吞。
    """

    def __init__(self, msg: str, code: str = "PLATFORM_ERROR",
                 request_id: str = "", http_status: int = 502):
        super().__init__(msg)
        self.msg = msg
        self.code = code
        self.request_id = request_id
        self.http_status = http_status

    def detail(self, instance_id: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "error_code": self.code,
            "msg": self.msg,
            "request_id": self.request_id,
            "instance": instance_id,
        }


@dataclass
class CloudInstance:
    """一台云实例（运行态在内存，不持久化）。"""

    id: str                      # autodl:pro-7889ca37d10f（UI 展示"平台:实例ID"）
    platform: str                # autodl
    instance_uuid: str
    worker_url: str              # 必须与 COMFYUI_WORKERS 中 url 严格一致
    api_base: str = ""
    api_token: str = ""
    display_name: str = ""
    boot_command: str = ""
    ready_timeout: int = 600
    ready_poll: int = 10
    allow_power_off: bool = True
    env_file: str = ""

    # ---- 运行态 ----
    instance_status: Optional[str] = None   # running / shutdown / ...
    op_state: str = "idle"                  # idle | starting | stopping
    last_error: str = ""
    last_op: Dict[str, Any] = field(default_factory=dict)  # 最近一次开关机/状态查询结果（白名单，无凭据）
    last_op_at: float = 0.0
    last_status_at: float = 0.0
    last_spec_at: float = 0.0
    spec: Dict[str, Any] = field(default_factory=dict)
    node: Any = None                        # WorkerNode（bind_nodes 时挂上）
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def controllable(self) -> bool:
        return bool(self.api_token and self.instance_uuid)

    @property
    def worker_healthy(self) -> bool:
        return bool(self.node is not None and getattr(self.node, "healthy", False))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cloud_id": self.id,
            "platform": self.platform,
            "instance_id": self.id,          # 形如 autodl:pro-7889ca37d10f
            "instance_uuid": self.instance_uuid,
            "worker_url": self.worker_url,
            "display_name": self.display_name or self.id,
            "instance_status": self.instance_status,
            "op_state": self.op_state,
            "worker_healthy": self.worker_healthy,
            "power_controllable": self.controllable,
            "allow_power_off": self.allow_power_off,
            "boot_command": self.boot_command,
            "ready_timeout": self.ready_timeout,
            "last_error": self.last_error,
            "last_op": self.last_op,
            "spec": self.spec,
        }


class CloudProvider:
    """平台 Provider 抽象：实现 status / power_on / power_off / snapshot。"""

    name = "base"

    def headers(self, inst: CloudInstance) -> Dict[str, str]:
        raise NotImplementedError

    async def status(self, inst: CloudInstance) -> str:
        raise NotImplementedError

    async def power_on(self, inst: CloudInstance, start_command: Optional[str] = None) -> None:
        raise NotImplementedError

    async def power_off(self, inst: CloudInstance) -> None:
        raise NotImplementedError

    async def snapshot(self, inst: CloudInstance) -> Dict[str, Any]:
        """只读规格查询（默认空实现，平台可选）。"""
        return {}

"""AutoDL 实例开放 API（https://www.autodl.com/docs/instance_pro_api/）。

实测要点（2026-09-15 官方文档 + 真实请求核对，详见《autodl-开关机-API错误返回实测》）：
- 鉴权头 `Authorization: <token>`（**不带 Bearer**）
- 统一返回体 {"code": "Success"|..., "data": ..., "msg": ..., "request_id": ...}
- `/status`、`/snapshot` 是 **GET + query**（instance_uuid）；用 POST 会 404
- `/power_on` body 需 {instance_uuid, payload: "gpu"}（payload 必填），start_command 选填
- `/power_off` body 仅 {instance_uuid}
- **成败只看 body 的 code，不能看 HTTP 状态码**（失败也返回 HTTP 200）
- snapshot 含 root_password/jupyter_token 等敏感字段 → 只取白名单字段对外
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from .base import CloudAPIError, CloudInstance, CloudProvider

logger = logging.getLogger(__name__)

# snapshot 白名单（**绝不透传 root_password / jupyter_token / ssh_command 等凭据**）
_SNAPSHOT_FIELDS = (
    ("region_sign", "region"),
    ("snapshot_gpu_alias_name", "gpu"),
    ("payg_price", "payg_price"),
    ("origin_pay_price", "origin_pay_price"),
    ("cpu_arch", "cpu_arch"),
    ("chip_corp", "chip_corp"),
    ("expand_system_disk_size", "system_disk_bytes"),
)

_CODE_MAP = {
    "AuthorizeFailed": "AUTH_FAILED",
    "RecordNotFoundError": "NOT_FOUND",
    "InternalError": "PLATFORM_ERROR",
    "ParamError": "BAD_REQUEST",
}


def _map_error_code(platform_code: str, msg: str) -> str:
    """平台 code → 我方 error_code（UI 文案映射见实测文档 §2.3）。"""
    if any(k in msg for k in ("暂无库存", "无卡", "库存")):
        return "NO_STOCK"
    return _CODE_MAP.get(platform_code, "PLATFORM_ERROR")


class AutoDLProvider(CloudProvider):
    name = "autodl"
    TIMEOUT = 20

    def headers(self, inst: CloudInstance) -> Dict[str, str]:
        return {"Authorization": inst.api_token, "Content-Type": "application/json"}

    async def _call(self, inst: CloudInstance, method: str, path: str,
                    params: Optional[dict] = None, json_body: Optional[dict] = None) -> Any:
        url = f"{inst.api_base.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                resp = await client.request(method, url, params=params, json=json_body,
                                            headers=self.headers(inst))
        except httpx.TimeoutException:
            raise CloudAPIError(f"平台接口超时（{path}）", "UPSTREAM_TIMEOUT", http_status=504)
        except httpx.HTTPError as exc:
            raise CloudAPIError(f"平台网络错误：{exc.__class__.__name__}", "UPSTREAM_ERROR",
                                http_status=502)
        try:
            body = resp.json()
        except Exception:
            raise CloudAPIError(f"平台返回非 JSON（HTTP {resp.status_code}）", "UPSTREAM_ERROR",
                                http_status=502)
        code = str(body.get("code") or "")
        request_id = str(body.get("request_id") or "")
        if code.lower() != "success":
            msg = str(body.get("msg") or f"平台返回 code={code or resp.status_code}")
            # 日志不打印 token / 请求体凭据；只记实例与 request_id
            logger.warning("AutoDL API 失败: %s %s instance=%s code=%s request_id=%s msg=%s",
                           method, path, inst.instance_uuid, code, request_id, msg)
            raise CloudAPIError(msg, _map_error_code(code, msg), request_id, http_status=502)
        return body.get("data")

    async def status(self, inst: CloudInstance) -> str:
        data = await self._call(inst, "GET", "/api/v1/dev/instance/pro/status",
                                params={"instance_uuid": inst.instance_uuid})
        return str(data)

    async def snapshot(self, inst: CloudInstance) -> Dict[str, Any]:
        data = await self._call(inst, "GET", "/api/v1/dev/instance/pro/snapshot",
                                params={"instance_uuid": inst.instance_uuid})
        if not isinstance(data, dict):
            return {}
        return {out_key: data.get(api_key) for api_key, out_key in _SNAPSHOT_FIELDS}

    async def power_on(self, inst: CloudInstance, start_command: Optional[str] = None) -> None:
        # payload 必填；本地先断言，避免"漏参数"被平台伪装成"无库存"（实测 #5）
        assert inst.instance_uuid, "缺少 instance_uuid"
        assert inst.api_token, "缺少 CLOUD_API_TOKEN"
        body: Dict[str, Any] = {"instance_uuid": inst.instance_uuid, "payload": "gpu"}
        if start_command:
            body["start_command"] = start_command
        await self._call(inst, "POST", "/api/v1/dev/instance/pro/power_on", json_body=body)

    async def power_off(self, inst: CloudInstance) -> None:
        assert inst.instance_uuid, "缺少 instance_uuid"
        assert inst.api_token, "缺少 CLOUD_API_TOKEN"
        await self._call(inst, "POST", "/api/v1/dev/instance/pro/power_off",
                         json_body={"instance_uuid": inst.instance_uuid})

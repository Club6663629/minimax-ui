"""codex-gateway v2.0.0 客户端（内网回环 API）。

职责边界（改这个文件前先读一遍，避免平台又长出手搓）：
- 网关负责：拼 prompt、跑 skill、跑脚本门、写产物文件；
- 本模块负责：HTTP + Bearer + **错误分类**，只做「把网关的答复翻译成 Python 异常」；
- 平台负责：鉴权、提交、轮询、下载落盘、纯字段搬运、像素生成、界面。

安全前提（网关是 danger-full-access + root，等于「持 token 即 root」）：
- 网关只绑 `127.0.0.1:8910`，公网 `/v1/*` 与 `/healthz` 已 404 → 后端必须与网关同机；
- token 只从文件或环境变量读取，**绝不入库、绝不进任何接口响应**；
- 契约若变（网关后续由运维/其它 agent 调整），只改这一个文件。
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# 网关 stage 白名单（`POST /v1/jobs` 只认这 7 个，其余 400）
STAGES: Dict[str, str] = {
    "outline": "novel-outline",
    "cast": "novel-characters",
    "art": "novel-art",
    "refs": "character-refs",
    "script": "novel-script",
    "staging": "novel-staging",
    "storyboard": "novel-storyboard",
}
GATEWAY_STAGES = tuple(STAGES.values())

# `timeout_kind` 四类：前端必须分开提示，平台必须分开重试
TIMEOUT_TOTAL = "TIMEOUT total"
TIMEOUT_SILENCE = "TIMEOUT silence"
CANCELLED_BY_API = "CANCELLED by API"
INTERRUPTED_BY_RESTART = "INTERRUPTED by restart"


class CodexError(Exception):
    """网关交互失败的基类；`status` 是 HTTP 状态码（0 = 没拿到响应）。"""

    def __init__(self, message: str, *, status: int = 0, payload: Optional[Dict] = None):
        super().__init__(message)
        self.message = str(message)
        self.status = int(status or 0)
        self.payload = payload or {}

    def as_dict(self) -> Dict:
        return {"error": self.message, "status": self.status}


class CodexUnavailable(CodexError):
    """网关不可达 / 未启用（503 语义）：删掉本地骨架后，这就是「不可用」，不许假装成功。"""


class CodexAuthError(CodexError):
    """401 / 403：token 缺失、失效或越权。"""


class CodexNotFound(CodexError):
    """404：job 或产物不存在。"""


class CodexConflict(CodexError):
    """409：网关侧状态冲突（如重复取消）。"""


class CodexBadRequest(CodexError):
    """400：stage / input 等参数被网关拒绝。"""


class CodexJobFailed(CodexError):
    """job 本身 failed：按 `timeout_kind` 区分超时、取消、被重启打断。"""

    def __init__(self, message: str, *, timeout_kind: str = "", detail: Optional[Dict] = None):
        super().__init__(message, status=0, payload=detail or {})
        self.timeout_kind = timeout_kind or ""

    @property
    def retryable(self) -> bool:
        """`CANCELLED by API` 不重试；其余（内容失败 / 静默超时 / 被重启打断）可重试。"""
        return self.timeout_kind != CANCELLED_BY_API


# ------------------------------------------------------------------ 配置
def enabled() -> bool:
    return bool(getattr(settings, "codex_gw_enabled", False))


def base_url() -> str:
    return str(getattr(settings, "codex_gw_url", "") or "").strip().rstrip("/")


def token() -> str:
    """读 token：优先环境变量/配置，其次令牌文件。**不要把这个值写进日志或响应。**"""
    raw = str(getattr(settings, "codex_gw_token", "") or "").strip()
    if raw:
        return raw
    path = str(getattr(settings, "codex_gw_token_file", "") or "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def token_present() -> bool:
    return bool(token())


def status() -> Dict:
    """链路状态（不含 token 本身）。`available=False` 时前端必须红态并禁用提交。"""
    url = base_url()
    return {
        "enabled": enabled(),
        "url": url,
        "tokenPresent": token_present(),
        "tokenFile": str(getattr(settings, "codex_gw_token_file", "") or ""),
        "available": bool(enabled() and url and token_present()),
        "stages": dict(STAGES),
        "note": "" if token_present() else "网关 token 未配置（CODEX_GW_TOKEN / CODEX_GW_TOKEN_FILE）",
    }


def _headers() -> Dict[str, str]:
    tok = token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(float(getattr(settings, "codex_gw_timeout", 30.0) or 30.0))


def _raise_for(resp: httpx.Response) -> None:
    """按状态码分门别类地抛，别让调用方去猜 401 和 500。"""
    if resp.status_code < 400:
        return
    try:
        payload = resp.json()
    except ValueError:
        payload = {"detail": (resp.text or "")[:500]}
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("detail") or payload.get("error") or payload)[:500]
    code = resp.status_code
    if code in (401, 403):
        raise CodexAuthError(f"网关鉴权失败（{code}）：{detail}", status=code, payload=payload)
    if code == 404:
        raise CodexNotFound(f"网关资源不存在（404）：{detail}", status=code, payload=payload)
    if code == 409:
        raise CodexConflict(f"网关状态冲突（409）：{detail}", status=code, payload=payload)
    if code == 400:
        raise CodexBadRequest(f"网关拒绝请求（400）：{detail}", status=code, payload=payload)
    if code in (502, 503, 504):
        raise CodexUnavailable(f"网关暂不可用（{code}）：{detail}", status=code, payload=payload)
    raise CodexError(f"网关错误（{code}）：{detail}", status=code, payload=payload)


async def _request(method: str, path: str, *, json_body: Optional[Dict] = None,
                   auth: bool = True) -> httpx.Response:
    if not enabled():
        raise CodexUnavailable("codex 网关未启用（CODEX_GW_ENABLED=false）")
    url = base_url()
    if not url:
        raise CodexUnavailable("codex 网关地址为空（CODEX_GW_URL）")
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.request(
                method, f"{url}{path}", json=json_body,
                headers=_headers() if auth else None)
    except httpx.HTTPError as exc:
        raise CodexUnavailable(f"codex 网关不可达：{exc}") from exc
    _raise_for(resp)
    return resp


# ------------------------------------------------------------------ 端点
async def healthz() -> Dict:
    """`GET /healthz`（免鉴权，含依赖自检：codex 版本 / skills 数 / deepseek / 磁盘）。"""
    resp = await _request("GET", "/healthz", auth=False)
    try:
        data = resp.json()
    except ValueError:
        return {"raw": (resp.text or "")[:2000]}
    return data if isinstance(data, dict) else {"raw": data}


def _norm_job(data: Any) -> Dict:
    """网关回包归一：v1 的作业 id 字段叫 `job_id`，别处文档又叫 `id`，两边都认。

    调用方只读 `row["id"]` / `row["job_id"]`（两个键都会被填上），
    状态别名 `state` 也归一到 `status`，避免每处调用都写一遍兼容。
    """
    if not isinstance(data, dict):
        return {}
    row = dict(data)
    inner = row.get("job")
    if isinstance(inner, dict):
        row = {**inner, **row}
    jid = str(row.get("job_id") or row.get("id") or "")
    if jid:
        row["job_id"] = jid
        row["id"] = jid
    if not row.get("status") and row.get("state"):
        row["status"] = row["state"]
    return row


async def health() -> Dict:
    resp = await _request("GET", "/v1/health")
    try:
        data = resp.json()
    except ValueError:
        return {"raw": (resp.text or "")[:2000]}
    return data if isinstance(data, dict) else {"raw": data}


async def skills() -> List:
    """`GET /v1/skills`：正常情况下 7 个 stage。"""
    resp = await _request("GET", "/v1/skills")
    try:
        data = resp.json()
    except ValueError:
        return []
    if isinstance(data, dict):
        rows = data.get("skills") or data.get("items") or []
    else:
        rows = data
    return rows if isinstance(rows, list) else []


async def submit(
    *,
    stage: str,
    input: str,                                         # noqa: A002 —— 网关字段名就叫 input
    project_id: str = "default",
    reasoning_effort: str = "",
    timeout: Optional[int] = None,
    silence_timeout: Optional[int] = None,
) -> Dict:
    """`POST /v1/jobs`。字段与网关严格对齐：**没有 attach**，上游一律写绝对路径进 input。"""
    if stage not in GATEWAY_STAGES:
        raise CodexBadRequest(f"未知 stage {stage}（可选：{', '.join(GATEWAY_STAGES)}）")
    text = (input or "").strip()
    if len(text) < 5:
        raise CodexBadRequest("input 至少 5 个字符（网关会 400）")
    body = {
        "stage": stage,
        "input": text,
        "project_id": (project_id or "default").strip() or "default",
        "reasoning_effort": (reasoning_effort
                             or str(getattr(settings, "codex_gw_default_effort", "medium"))).strip(),
        "timeout": int(timeout or getattr(settings, "codex_gw_job_timeout", 1800)),
        "silence_timeout": int(silence_timeout
                               or getattr(settings, "codex_gw_silence_timeout", 600)),
    }
    resp = await _request("POST", "/v1/jobs", json_body=body)
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {"raw": (resp.text or "")[:2000]}
    row = _norm_job(data)
    if not row.get("id"):
        raise CodexError("网关未返回 job id", status=resp.status_code, payload={"raw": data})
    return row


async def job(job_id: str) -> Dict:
    resp = await _request("GET", f"/v1/jobs/{quote(str(job_id), safe='')}")
    try:
        return _norm_job(resp.json())
    except ValueError:
        return {}


async def jobs(limit: int = 50, status: str = "") -> List[Dict]:
    """`GET /v1/jobs?limit&status`：重启自愈时要拿 queued|running 全表做核对。"""
    path = f"/v1/jobs?limit={int(max(1, min(500, limit)))}"
    if status:
        path += f"&status={quote(str(status), safe='')}"
    resp = await _request("GET", path)
    data = resp.json()
    if isinstance(data, dict):
        rows = data.get("jobs") or data.get("items") or []
    else:
        rows = data
    return [_norm_job(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def cancel(job_id: str) -> Dict:
    """`POST /v1/jobs/{id}/cancel`：运行中杀进程组 / 排队中直接出队。"""
    resp = await _request("POST", f"/v1/jobs/{quote(str(job_id), safe='')}/cancel")
    try:
        return resp.json() if resp.content else {}
    except ValueError:
        return {}


async def delete(job_id: str) -> None:
    """`DELETE /v1/jobs/{id}`：**只归档记录、不删 workdir**。平台正常流程不调用它。"""
    await _request("DELETE", f"/v1/jobs/{quote(str(job_id), safe='')}")


def artifact_url(job_id: str, path: str) -> str:
    return f"{base_url()}/v1/jobs/{quote(str(job_id), safe='')}/artifacts/{quote(path, safe='/')}"


async def artifact_bytes(job_id: str, path: str) -> bytes:
    resp = await _request(
        "GET", f"/v1/jobs/{quote(str(job_id), safe='')}/artifacts/{quote(path, safe='/')}")
    return resp.content


async def download_all(job_id: str, dest_dir: Path, *,
                       artifacts: Optional[List[Dict]] = None) -> List[Dict]:
    """把 job 的全部产物拉到 `dest_dir`（保持网关侧相对路径）。

    产物清单优先用轮询结果里的 `artifacts[{path,abs,bytes}]`；只给 `abs` 时按 `abs`
    去掉 `/work/` 前缀还原相对路径。单个文件失败只记日志，不拖垮整批（部分产物仍可用）。
    """
    rows = artifacts
    if rows is None:
        rows = (await job(job_id)).get("artifacts") or []
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: List[Dict] = []
    for row in rows or []:
        if isinstance(row, str):                      # 只回路径时也当一条产物
            row = {"path": row}
        rel = str(row.get("path") or "").lstrip("/")
        if not rel:
            abs_path = str(row.get("abs") or "")
            rel = abs_path.split("/work/", 1)[1] if "/work/" in abs_path else ""
        if not rel or rel.startswith("..") or "/../" in rel:
            continue
        try:
            content = await artifact_bytes(job_id, rel)
        except CodexError as exc:
            logger.warning("下载网关产物失败 %s/%s：%s", job_id, rel, exc)
            saved.append({"path": rel, "bytes": 0, "error": exc.message})
            continue
        out = dest_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_bytes(content)
        except OSError as exc:
            logger.warning("写入网关产物失败 %s：%s", out, exc)
            saved.append({"path": rel, "bytes": 0, "error": str(exc)})
            continue
        saved.append({"path": rel, "bytes": len(content)})
    return saved


async def stats() -> Dict:
    """`GET /v1/stats`：按 stage 汇总 tokens / 耗时（对账用）。"""
    resp = await _request("GET", "/v1/stats")
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def gateway_project_id(project_id: int) -> str:
    """网关侧 project_id 命名空间：`hibtv-<平台 project.id>`。"""
    prefix = str(getattr(settings, "codex_gw_project_prefix", "hibtv-") or "hibtv-")
    return f"{prefix}{int(project_id)}"


def remote_dir(project_id: int, job_id: str) -> str:
    """网关机器上的产物根（只用于拼给 codex 的 input 与日志，不用于本机 IO）。"""
    return f"/data/projects/{gateway_project_id(project_id)}/{job_id}/work"


__all__ = [
    "STAGES", "GATEWAY_STAGES", "CodexError", "CodexUnavailable", "CodexAuthError",
    "CodexNotFound", "CodexConflict", "CodexBadRequest", "CodexJobFailed",
    "TIMEOUT_TOTAL", "TIMEOUT_SILENCE", "CANCELLED_BY_API", "INTERRUPTED_BY_RESTART",
    "enabled", "status", "token_present", "healthz", "health", "skills", "submit",
    "job", "jobs", "cancel", "delete", "artifact_url", "artifact_bytes", "download_all",
    "stats", "gateway_project_id", "remote_dir", "base_url",
]

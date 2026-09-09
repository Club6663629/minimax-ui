"""MiniMax 云端 API 客户端：H3-Context-IR 提示词增强。

均为异步任务：提交拿 task_id → 轮询结果（指数退避，≤3 次重试）。
注意：接口路径与字段以 MiniMax 开放平台当日文档为准，联调时如有出入
只需修改本文件常量。未配置 MINIMAX_API_KEY 时不会调用本模块。

云端仅保留提示词增强（Context-IR）；生成本体与超分均走本地 worker，
已移除云端生成 / 2K 重生成降级通道。
"""
import asyncio
import logging
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)


class CloudAPIError(Exception):
    pass


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.minimax_api_key}"}


def _check_base_resp(data: dict) -> None:
    """MiniMax 接口统一返回 base_resp，status_code 0 为成功。"""
    resp = data.get("base_resp") or {}
    if resp.get("status_code", 0) != 0:
        raise CloudAPIError(f"云端接口错误: {resp.get('status_msg', resp)}")


async def _post_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> dict:
    last_exc: Optional[Exception] = None
    for attempt in range(3):  # 指数退避重试 ≤3 次
        try:
            r = await client.post(url, headers=_headers(), timeout=60, **kwargs)
            r.raise_for_status()
            data = r.json()
            _check_base_resp(data)
            return data
        except (httpx.HTTPError, CloudAPIError, ValueError) as exc:
            last_exc = exc
            await asyncio.sleep(2**attempt)
    raise CloudAPIError(f"请求失败（已重试 3 次）: {last_exc}")


async def enhance_prompt(prompt: str, duration: int, ratio: str) -> str:
    """提示词上下文增强：返回增强后的提示词。超时上限 10 分钟。"""
    base = settings.minimax_api_base.rstrip("/")
    async with httpx.AsyncClient() as client:
        data = await _post_with_retry(
            client,
            f"{base}/v2/h3_context_ir",
            json={
                "model": "MiniMax-H3",
                "content": [{"type": "text", "text": prompt}],
                "duration": duration,
                "ratio": ratio,
            },
        )
        task_id = data.get("task_id")
        if not task_id:
            raise CloudAPIError(f"增强任务未返回 task_id: {data}")

        deadline = asyncio.get_event_loop().time() + 600  # 10 分钟
        interval = 3.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 15.0)
            r = await client.get(
                f"{base}/v2/query/video_generation/{task_id}",
                headers=_headers(),
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()
            task = result.get("task") or result
            status_str = (task.get("status") or "").strip().lower()
            if status_str in ("succeeded", "success", "done", "completed"):
                content = task.get("content") or {}
                enhanced = content.get("prompt") or content.get("text") or ""
                if not enhanced:
                    raise CloudAPIError(f"增强结果为空: {result}")
                return enhanced
            if status_str in ("fail", "failed", "error"):
                raise CloudAPIError(f"增强任务失败: {result}")
    raise CloudAPIError("增强任务超时（10 分钟）")

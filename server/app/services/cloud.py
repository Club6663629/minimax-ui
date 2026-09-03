"""MiniMax 云端 API 客户端：H3-Context-IR 提示词增强 / H3-Regenerate-2K / 云端生成降级。

均为异步任务：提交拿 task_id → 轮询结果（指数退避，≤3 次重试）。
注意：接口路径与字段以 MiniMax 开放平台当日文档为准，联调时如有出入
只需修改本文件常量。未配置 MINIMAX_API_KEY 时不会调用本模块。
"""
import asyncio
import base64
import logging
from pathlib import Path
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
    """① 上下文增强：返回增强后的提示词。超时上限 10 分钟。"""
    base = settings.minimax_api_base.rstrip("/")
    async with httpx.AsyncClient() as client:
        data = await _post_with_retry(
            client,
            f"{base}/v2/h3_context_ir",
            json={
                "model": "H3-Context-IR",
                "content": prompt,
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
                f"{base}/v1/query/h3_context_ir",
                headers=_headers(),
                params={"task_id": task_id},
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()
            _check_base_resp(result)
            status_str = result.get("status") or result.get("task_status", "")
            if status_str in ("Success", "Success "):
                content = result.get("content") or {}
                enhanced = content.get("prompt") or content.get("text") or ""
                if not enhanced:
                    raise CloudAPIError(f"增强结果为空: {result}")
                return enhanced
            if status_str in ("Fail", "Failed"):
                raise CloudAPIError(f"增强任务失败: {result}")
    raise CloudAPIError("增强任务超时（10 分钟）")


async def generate_video(
    prompt: str,
    duration: int,
    aspect_ratio: str,
    first_frame: Optional[Path] = None,
    last_frame: Optional[Path] = None,
) -> Path:
    """云端全流程降级通道：官方 API 直接出成片（含提示词已增强后的最终提示词）。

    仅在本地生成队列深度超阈值时由调度器触发。接口路径/字段以开放平台当日文档为准。
    返回下载到 staging 目录的成片路径（调用方负责移动到 output）。
    """
    base = settings.minimax_api_base.rstrip("/")
    payload: dict = {
        "model": "MiniMax-H3",
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
    }
    if first_frame:
        payload["first_frame_image"] = base64.b64encode(first_frame.read_bytes()).decode()
    if last_frame:
        payload["last_frame_image"] = base64.b64encode(last_frame.read_bytes()).decode()

    async with httpx.AsyncClient() as client:
        data = await _post_with_retry(client, f"{base}/v2/h3_video_generation", json=payload)
        task_id = data.get("task_id")
        if not task_id:
            raise CloudAPIError(f"云端生成未返回 task_id: {data}")

        deadline = asyncio.get_event_loop().time() + 1800  # 30 分钟
        interval = 5.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 20.0)
            r = await client.get(
                f"{base}/v1/query/h3_video_generation",
                headers=_headers(),
                params={"task_id": task_id},
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()
            _check_base_resp(result)
            status_str = result.get("status") or result.get("task_status", "")
            if status_str in ("Success", "Success "):
                file_url = result.get("file_url") or result.get("video_url", "")
                if not file_url:
                    raise CloudAPIError(f"云端生成结果缺少下载地址: {result}")
                vr = await client.get(file_url, timeout=300)
                vr.raise_for_status()
                from ..config import STAGING_DIR
                out = STAGING_DIR / f"cloud_{task_id}.mp4"
                out.write_bytes(vr.content)
                logger.info("云端降级成片已下载: %s", out)
                return out
            if status_str in ("Fail", "Failed"):
                raise CloudAPIError(f"云端生成任务失败: {result}")
    raise CloudAPIError("云端生成任务超时（30 分钟）")


async def regenerate_2k(video_path: Path, aspect_ratio: str, duration: int) -> Path:
    """③ 2K 重生成：上传本地 768p 成片 → 轮询 → 下载 2K 成片。

    返回下载到本地的 2K 文件路径（调用方负责移动到 output 目录）。
    超时上限 30 分钟。
    """
    base = settings.minimax_api_base.rstrip("/")
    staging = video_path.parent
    async with httpx.AsyncClient() as client:
        with open(video_path, "rb") as f:
            data = await _post_with_retry(
                client,
                f"{base}/v2/h3_regenerate_2k",
                data={
                    "model": "H3-Regenerate-2K",
                    "aspect_ratio": aspect_ratio,
                    "duration": duration,
                },
                files={"video": (video_path.name, f, "video/mp4")},
            )
        task_id = data.get("task_id")
        if not task_id:
            raise CloudAPIError(f"2K 任务未返回 task_id: {data}")

        deadline = asyncio.get_event_loop().time() + 1800  # 30 分钟
        interval = 5.0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(interval)
            interval = min(interval * 1.5, 20.0)
            r = await client.get(
                f"{base}/v1/query/h3_regenerate_2k",
                headers=_headers(),
                params={"task_id": task_id},
                timeout=30,
            )
            r.raise_for_status()
            result = r.json()
            _check_base_resp(result)
            status_str = result.get("status") or result.get("task_status", "")
            if status_str in ("Success", "Success "):
                file_url = result.get("file_url") or result.get("video_url", "")
                if not file_url:
                    raise CloudAPIError(f"2K 结果缺少下载地址: {result}")
                vr = await client.get(file_url, timeout=300)
                vr.raise_for_status()
                out = staging / f"{video_path.stem}_2k.mp4"
                out.write_bytes(vr.content)
                logger.info("2K 成片已下载: %s", out)
                return out
            if status_str in ("Fail", "Failed"):
                raise CloudAPIError(f"2K 任务失败: {result}")
    raise CloudAPIError("2K 任务超时（30 分钟）")

"""ComfyUI 客户端：工作流模板加载与注入、提交、轮询、取产物。

工作流模板位于 server/workflows/{mode}_api.json，由官方模板
（Comfy-Org/workflow_templates，见 _official/convert.py）转换而来，覆盖：
- t2v   文生视频
- flf2v 首/尾帧生视频（first_frame / last_frame 可只提供其一）
- r2v   全能参考生视频（最多 9 张参考图，提示词用 <Picture N> 按序引用）

注入策略（对齐官方原生节点约定）：
- ResolutionSelector → 画幅预设 + 0.98 百万像素（768p 原生画布，16:9 即 1344x768）
- PrimitiveFloat → 时长秒数（帧数由工作流表达式按 24fps / 17 帧块自动吸附）
- PrimitiveStringMultiline 或 "prompt" 字符串输入 → 注入提示词
- seed / noise_seed → 随机化
- LoadImage → 按模式动态重建（首/尾帧 或 多张参考图）
"""
import asyncio
import json
import logging
import random
from pathlib import Path
from typing import List, Optional

import httpx

from ..config import WORKFLOW_DIR, settings

logger = logging.getLogger(__name__)


class ComfyUIError(Exception):
    pass


# 官方 ResolutionSelector 画幅预设；0.98 百万像素即 H3 原生 768p 画布（跳过 1.0 档，官方提示会超面积上限）
ASPECT_PRESETS = {
    "16:9": "16:9 (Widescreen)",
    "9:16": "9:16 (Portrait Widescreen)",
    "1:1": "1:1 (Square)",
}
MEGAPIXELS_768P = 0.98


def load_workflow(mode: str) -> dict:
    path = WORKFLOW_DIR / f"{mode}_api.json"
    if not path.exists():
        raise ComfyUIError(f"缺少工作流模板: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _add_load_image(workflow: dict, image_name: str) -> list:
    """动态追加一个 LoadImage 节点，返回连线引用 [节点ID, 0]。"""
    nums = [int(k) for k in workflow if str(k).isdigit()]
    nid = str((max(nums) if nums else 9000) + 1)
    workflow[nid] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
        "_meta": {"title": "LoadImage"},
    }
    return [nid, 0]


def inject(
    workflow: dict,
    mode: str,
    prompt: str,
    aspect_ratio: str,
    duration: int,
    image_names: Optional[List[str]] = None,
) -> dict:
    """把提示词 / 画幅 / 时长 / 输入图注入官方工作流模板（就地修改并返回）。"""
    image_names = list(image_names or [])

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = node.get("class_type", "")

        if class_type == "ResolutionSelector":
            inputs["aspect_ratio"] = ASPECT_PRESETS.get(aspect_ratio, ASPECT_PRESETS["16:9"])
            inputs["megapixels"] = MEGAPIXELS_768P
        elif class_type == "PrimitiveFloat":  # 工作流中的时长（秒）参数
            inputs["value"] = float(duration)
        elif class_type == "PrimitiveStringMultiline":  # r2v 的提示词源节点
            inputs["value"] = prompt

        for key, value in inputs.items():
            if key in ("seed", "noise_seed") and isinstance(value, int):
                inputs[key] = random.randint(0, 2**63 - 1)
        if isinstance(inputs.get("prompt"), str):
            inputs["prompt"] = prompt

    if mode == "flf2v":
        h3 = next(
            (n for n in workflow.values() if n.get("class_type") == "MiniMaxH3ImageToVideo"),
            None,
        )
        if h3 is not None:
            # 清掉模板默认首帧，按用户输入重建（首/尾帧均可单独提供）
            for nid in [k for k, n in workflow.items() if n.get("class_type") == "LoadImage"]:
                del workflow[nid]
            for socket in ("first_frame", "last_frame"):
                h3["inputs"].pop(socket, None)
            first, last = (image_names + [None, None])[:2]
            if first:
                h3["inputs"]["first_frame"] = _add_load_image(workflow, first)
            if last:
                h3["inputs"]["last_frame"] = _add_load_image(workflow, last)
    elif mode == "r2v":
        h3 = next(
            (n for n in workflow.values() if n.get("class_type") == "MiniMaxH3ReferenceToVideo"),
            None,
        )
        if h3 is not None:
            # 清掉模板默认参考图，按用户上传数量重建 ref_images.ref_image_N
            for nid in [k for k, n in workflow.items() if n.get("class_type") == "LoadImage"]:
                del workflow[nid]
            for key in [k for k in h3["inputs"] if k.startswith("ref_images.")]:
                del h3["inputs"][key]
            for i, name in enumerate(image_names):
                h3["inputs"][f"ref_images.ref_image_{i}"] = _add_load_image(workflow, name)

    return workflow


class ComfyUIClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base = (base_url or settings.comfyui_url).rstrip("/")

    async def upload_image(self, path: Path) -> str:
        """上传图片到 ComfyUI 的 input 目录，返回其文件名。"""
        async with httpx.AsyncClient(timeout=120) as client:
            with open(path, "rb") as f:
                r = await client.post(
                    f"{self.base}/upload/image",
                    files={"image": (path.name, f, "image/png")},
                    data={"overwrite": "true"},
                )
        if r.status_code != 200:
            raise ComfyUIError(f"上传图片到 ComfyUI 失败: {r.text[:200]}")
        return r.json()["name"]

    async def submit(self, workflow: dict) -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base}/prompt",
                json={"prompt": workflow, "client_id": "minimax-ui"},
            )
        if r.status_code != 200:
            raise ComfyUIError(f"提交工作流失败: {r.text[:300]}")
        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"提交工作流未返回 prompt_id: {r.text[:200]}")
        return prompt_id

    async def wait_result(self, prompt_id: str) -> dict:
        """轮询 /history/{prompt_id}，完成后返回产物文件信息
        {"filename": ..., "subfolder": ..., "type": ...}。"""
        deadline_loops = int(
            settings.comfyui_timeout_minutes * 60 / settings.comfyui_poll_interval
        )
        async with httpx.AsyncClient(timeout=30) as client:
            for _ in range(deadline_loops):
                await asyncio.sleep(settings.comfyui_poll_interval)
                r = await client.get(f"{self.base}/history/{prompt_id}")
                if r.status_code != 200:
                    continue
                history = r.json()
                entry = history.get(prompt_id)
                if not entry:
                    continue
                status_info = entry.get("status") or {}
                if status_info.get("status_str") == "error":
                    messages = status_info.get("messages", [])
                    raise ComfyUIError(f"ComfyUI 执行出错: {messages}")
                if not status_info.get("completed"):
                    continue
                for outputs in entry.get("outputs", {}).values():
                    for key in ("videos", "gifs"):
                        files = outputs.get(key)
                        if files:
                            return files[0]
                raise ComfyUIError("任务完成但未找到视频产物，请检查工作流输出节点")
        raise ComfyUIError(f"ComfyUI 任务超时（{settings.comfyui_timeout_minutes} 分钟）")

    async def fetch_file(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.get(
                f"{self.base}/view",
                params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            )
        if r.status_code != 200:
            raise ComfyUIError(f"下载 ComfyUI 产物失败: {filename}")
        return r.content

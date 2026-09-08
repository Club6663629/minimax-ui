"""ComfyUI 客户端：工作流模板加载与注入、提交、轮询、取产物。

工作流模板位于 server/workflows/{mode}_api.json，由官方模板
（Comfy-Org/workflow_templates，见 _official/convert.py）转换而来，覆盖：
- t2v    文生视频
- flf2v  首/尾帧生视频（first_frame / last_frame 可只提供其一）
- r2v    全能参考生视频（最多 9 张参考图，提示词用 <Picture N> 按序引用）
- upscale       本地超分（SeedVR2 KSampler 管线模板，默认 3B FP16 权重，1K/2K 分时）
- upscale_7b    本地超分（SeedVR2 原生节点模板，默认 3B FP16 权重，resolution 直设）

注入策略（对齐官方原生节点约定）：
- ResolutionSelector → 画幅预设 + 0.98 百万像素（768p 原生画布，16:9 即 1344x768）
- PrimitiveFloat → 时长秒数（帧数由工作流表达式按 24fps / 17 帧块自动吸附）
- PrimitiveStringMultiline 或 "prompt" 字符串输入 → 注入提示词
- seed / noise_seed → 随机化（仅生成类工作流；超分走 inject_upscale 定点注入）
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

# ---- 超分（SeedVR2）分时参数，见《H3集群部署方案》§2 ----
UPSCALE_TIERS = {
    "1k": {"steps": 1, "short_side": 1080},   # 1920×1080 等比，~1.4×
    "2k": {"steps": 1, "short_side": 1440},   # 2560×1440 等比，~1.9×
}
# 768p 原生画布（0.98 百万像素）的各画幅尺寸，用于换算放大倍数
SOURCE_SIZES = {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (976, 976)}
# 7B 原生节点 SeedVR2VideoUpscaler.resolution 以 16:9 输出高度为基准；
# 9:16/1:1 竖屏时以宽为短边，需取长边值才能得到「短边=1080/1440」的等比输出（官方模板说明）
UPSCALE_7B_RESOLUTION = {
    "16:9": {"1k": 1080, "2k": 1440},
    "9:16": {"1k": 1920, "2k": 2560},
    "1:1": {"1k": 1080, "2k": 1440},
}
UPSCALE_FRAME_BATCH = 21       # 帧批（4n+1）
UPSCALE_TEMPORAL_OVERLAP = 3   # 时域重叠帧数


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
    unet_name: Optional[str] = None,
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
        elif class_type == "UNETLoader" and unet_name:
            inputs["unet_name"] = unet_name
            # fp8 权重必须配 fp8_e4m3fn，否则 default 按 fp16 加载报错
            if "fp8" in unet_name:
                inputs["weight_dtype"] = "fp8_e4m3fn"
        elif class_type == "PrimitiveBoolean" and unet_name and "fp8" in unet_name:
            # fp8 模型 + bf16 Lightning LoRA 不兼容（q_scale float8_e4m3fn 无法过 rms_rope 算子）
            # 51 等 fp8 节点禁用 LoRA，保持 20 步；int8 节点（A100）继续用 LoRA 加速
            inputs["value"] = False
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


def inject_upscale(
    workflow: dict,
    video_name: str,
    tier: str,
    aspect_ratio: str,
    unet_name: Optional[str] = None,
) -> dict:
    """超分（SeedVR2）定点注入，按模板节点体系自适应（就地修改并返回）。

    通用：
    - LoadVideo → 待超分的 768p 中间产物
    - ImageFromBatch → 模板演示用的截帧限制，生产需全片，直接移除并重接上下游
    3B INT8 官方模板衍生的 KSampler 管线（含 ResizeImageMaskNode，模板默认权重 3B FP16）：
    - KSampler → 档位采样步数 + 随机 seed（模板默认 1 步为保守值）
    - ResizeImageMaskNode → 按目标短边 / 源片短边换算放大倍数（等比）
    - PrimitiveBoolean(split_latent) → 开启分时；帧批 21、时域重叠 3 帧（方案 §2）
    - UNETLoader → 按节点配置注入权重（3B / 7B INT8 / GGUF），未配置则沿用模板默认 3B FP16
    原生节点模板（含 SeedVR2VideoUpscaler，模板默认权重 3B FP16）：
    - SeedVR2VideoUpscaler → resolution 按档位/画幅直设 + 随机 seed（节点内部自带时域分块）
    - ImageScale → 对齐源片尺寸（避免模板默认 720p 预缩放的意外裁切）
    - SeedVR2LoadDiTModel → 按节点配置注入权重，未配置则沿用模板默认 3B FP16
    """
    if tier not in UPSCALE_TIERS:
        raise ComfyUIError(f"未知超分档位: {tier}")
    spec = UPSCALE_TIERS[tier]
    src_w, src_h = SOURCE_SIZES.get(aspect_ratio, SOURCE_SIZES["16:9"])
    multiplier = round(spec["short_side"] / min(src_w, src_h), 2)

    _drop_image_from_batch(workflow)  # 两种模板都可能有截帧限制（7B 模板自带）

    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        if class_type == "LoadVideo":
            inputs.pop("video", None)  # 旧版参数名，新版已改为 file，防残留
            inputs["file"] = video_name
        # ---- 3B INT8 管线 ----
        elif class_type == "KSampler":
            inputs["steps"] = spec["steps"]
            inputs["seed"] = random.randint(0, 2**63 - 1)
        elif class_type == "ResizeImageMaskNode":
            # 新版（ComfyUI 0.34）节点为动态 combo 结构：resize_type 取 combo 值，
            # multiplier 以点分隔键名 resize_type.multiplier 提交（嵌套参数序列化），
            # scale_method 为顶层参数；旧版 method 字段需清理，否则提交时
            # 缺必填 scale_method 报 required_input_missing
            inputs.pop("resize_type.multiplier", None)
            inputs.pop("method", None)
            inputs["resize_type"] = "scale by multiplier"
            inputs["resize_type.multiplier"] = multiplier
            inputs["scale_method"] = "lanczos"
        elif class_type == "PrimitiveBoolean":  # Split Latent 开关：启用分时
            inputs["value"] = True
        elif class_type == "SeedVR2TemporalChunk":
            # 新版 ComfyUI 0.34：chunking_mode 为 DynamicCombo（嵌套 dict），
            # temporal_overlap 为顶层 int 输入；旧版 frame_batch_size/mode 已废弃
            # 2026-09-07：改为保留模板值（temporal_overlap/chunking_mode 由模板决定，
            # 支持 manual + frames_per_chunk），不再强制覆盖
            inputs.pop("frame_batch_size", None)
            inputs.pop("mode", None)
        elif class_type == "UNETLoader" and unet_name:
            inputs["unet_name"] = unet_name
            # fp8 权重必须配 fp8_e4m3fn，否则 default 按 fp16 加载报错
            if "fp8" in unet_name:
                inputs["weight_dtype"] = "fp8_e4m3fn"
        # ---- 原生节点管线 ----
        elif class_type == "SeedVR2VideoUpscaler":
            inputs["resolution"] = UPSCALE_7B_RESOLUTION.get(
                aspect_ratio, UPSCALE_7B_RESOLUTION["16:9"]
            )[tier]
            inputs["seed"] = random.randint(0, 2**63 - 1)
        elif class_type == "ImageScale":  # 保持源片尺寸，短边对齐由 resolution 控制
            inputs["width"] = src_w
            inputs["height"] = src_h
        elif class_type == "SeedVR2LoadDiTModel" and unet_name:
            inputs["dit_name"] = unet_name

    return workflow


def _drop_image_from_batch(workflow: dict) -> None:
    """移除 ImageFromBatch（模板演示限 96 帧），把其下游改接到它的图像源。"""
    ifb = next(
        ((nid, n) for nid, n in workflow.items()
         if isinstance(n, dict) and n.get("class_type") == "ImageFromBatch"),
        None,
    )
    if ifb is None:
        return
    nid, node = ifb
    src = node["inputs"].get("image")
    if isinstance(src, list) and len(src) == 2:  # 重接下游后删除（保留源节点）
        for other in workflow.values():
            if not isinstance(other, dict):
                continue
            for key, value in (other.get("inputs") or {}).items():
                if isinstance(value, list) and value == [nid, 0]:
                    other["inputs"][key] = src
    del workflow[nid]


class ComfyUIClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base = (base_url or settings.comfyui_url).rstrip("/")

    async def upload_file(self, path: Path) -> str:
        """上传输入文件（图片/视频）到 ComfyUI 的 input 目录，返回其文件名。"""
        mime = "video/mp4" if path.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv") else "image/png"
        async with httpx.AsyncClient(timeout=300) as client:
            with open(path, "rb") as f:
                r = await client.post(
                    f"{self.base}/upload/image",
                    files={"image": (path.name, f, mime)},
                    data={"overwrite": "true"},
                )
        if r.status_code != 200:
            raise ComfyUIError(f"上传文件到 ComfyUI 失败: {r.text[:200]}")
        return r.json()["name"]

    async def upload_image(self, path: Path) -> str:
        """上传图片到 ComfyUI 的 input 目录，返回其文件名。"""
        return await self.upload_file(path)

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
                # ComfyUI 0.34+ 的 SaveVideo 节点把视频产物输出到 "images" key
                # （animated 标记为 true，文件名是 .mp4/.webm 等视频后缀）；
                # 旧版/其它节点仍可能用 "videos"/"gifs"。依次探测，优先视频专用 key。
                for outputs in entry.get("outputs", {}).values():
                    for key in ("videos", "gifs"):
                        files = outputs.get(key)
                        if files:
                            return files[0]
                    # SaveVideo 新版：产物落在 images 列表，按视频后缀过滤，排除纯图片输出
                    images = outputs.get("images")
                    if isinstance(images, list):
                        for f in images:
                            # 跳过 LoadVideo 加载的输入视频（type=input），
                            # 否则会误把 768p 源片当成超分产物返回
                            if isinstance(f, dict) and f.get("type") == "input":
                                continue
                            if isinstance(f, dict) and f.get("filename", "").lower().endswith(
                                (".mp4", ".webm", ".mov", ".mkv", ".avi")
                            ):
                                return f
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

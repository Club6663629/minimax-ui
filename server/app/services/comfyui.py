"""ComfyUI 客户端：工作流模板加载与注入、提交、轮询、取产物。

工作流模板位于 server/workflows/{mode}_api.json，由官方模板
（Comfy-Org/workflow_templates，见 _official/convert.py）转换而来，覆盖：
- t2v    文生视频
- flf2v  首/尾帧生视频（first_frame / last_frame 可只提供其一）
- r2v    全能参考生视频（最多 9 张参考，含图片/视频/音频混传，提示词用 <Picture/Video/Audio N> 按类型按序引用）
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
    "1k": {
        "steps": 1,
        "short_side": 1080,   # 1920×1080 等比，~1.4×
        "target_size": {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)},
    },
    "2k": {
        "steps": 1,
        "short_side": 1440,   # 2560×1440 等比，~1.9×
        "target_size": {"16:9": (2560, 1440), "9:16": (1440, 2560), "1:1": (1440, 1440)},
    },
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


def _next_node_id(workflow: dict) -> str:
    """取工作流下一个可用节点 ID 字符串。"""
    nums = [int(k) for k in workflow if str(k).isdigit()]
    return str((max(nums) if nums else 9000) + 1)


def _add_load_image(workflow: dict, image_name: str) -> list:
    """动态追加一个 LoadImage 节点，返回连线引用 [节点ID, 0]。"""
    nid = _next_node_id(workflow)
    workflow[nid] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
        "_meta": {"title": "LoadImage"},
    }
    return [nid, 0]


def _add_video_ref(workflow: dict, video_name: str) -> tuple[list, list]:
    """动态追加 LoadVideo + GetVideoComponents 链条，返回 (images_ref, audio_ref)。

    LoadVideo 输出 slot0=VIDEO；GetVideoComponents 约定 slot0=images / slot1=audio /
    slot2=fps。视频参考的 IMAGE 走 GetVideoComponents slot0，其自带音轨走 slot1，故
    接视频参考时同步把 slot1 接到 ref_video_audios（同索引配对）。
    """
    load = _next_node_id(workflow)
    workflow[load] = {
        "class_type": "LoadVideo",
        "inputs": {"file": video_name},
        "_meta": {"title": "LoadVideo"},
    }
    gvc = _next_node_id(workflow)
    workflow[gvc] = {
        "class_type": "GetVideoComponents",
        "inputs": {"video": [load, 0]},
        "_meta": {"title": "GetVideoComponents"},
    }
    return [gvc, 0], [gvc, 1]


def _add_load_audio(workflow: dict, audio_name: str) -> list:
    """动态追加一个 LoadAudio 节点，返回 AUDIO 输出引用 [节点ID, 0]。

    注：LoadAudio 参数名（audio vs file）与输出 slot 需按部署的 ComfyUI 版本核对。
    """
    nid = _next_node_id(workflow)
    workflow[nid] = {
        "class_type": "LoadAudio",
        "inputs": {"audio": audio_name},
        "_meta": {"title": "LoadAudio"},
    }
    return [nid, 0]


def inject(
    workflow: dict,
    mode: str,
    prompt: str,
    aspect_ratio: str,
    duration: int,
    image_names: Optional[List[str]] = None,
    video_names: Optional[List[str]] = None,
    audio_names: Optional[List[str]] = None,
    unet_name: Optional[str] = None,
) -> dict:
    """把提示词 / 画幅 / 时长 / 输入图/视频/音频注入官方工作流模板（就地修改并返回）。

    r2v 模式下 image_names / video_names / audio_names 分别对应参考图(≤9)、
    参考视频(≤3，每条自动接其自带音轨)与独立参考音频(≤3)。
    """
    image_names = list(image_names or [])
    video_names = list(video_names or [])
    audio_names = list(audio_names or [])

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
            # 清掉模板默认参考节点（LoadImage / LoadVideo / GetVideoComponents / LoadAudio），
            # 按用户上传数量重建 ref_images / ref_videos / ref_video_audios / ref_audios
            for nid in [
                k for k, n in workflow.items()
                if n.get("class_type") in ("LoadImage", "LoadVideo", "GetVideoComponents", "LoadAudio")
            ]:
                del workflow[nid]
            for key in [k for k in h3["inputs"] if k.startswith(("ref_images.", "ref_videos.", "ref_video_audios.", "ref_audios."))]:
                del h3["inputs"][key]
            # 参考图
            for i, name in enumerate(image_names):
                h3["inputs"][f"ref_images.ref_image_{i}"] = _add_load_image(workflow, name)
            # 参考视频：每条自动把其自带音轨接到 ref_video_audios（与 ref_videos 同索引配对）
            for i, name in enumerate(video_names):
                img_ref, aud_ref = _add_video_ref(workflow, name)
                h3["inputs"][f"ref_videos.ref_video_{i}"] = img_ref
                h3["inputs"][f"ref_video_audios.ref_video_audio_{i}"] = aud_ref
            # 独立参考音频
            for i, name in enumerate(audio_names):
                h3["inputs"][f"ref_audios.ref_audio_{i}"] = _add_load_audio(workflow, name)

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
    target_w, target_h = spec["target_size"].get(
        aspect_ratio, spec["target_size"]["16:9"]
    )

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

    # 3B INT8 KSampler 管线：multiplier 放大后追加 ImageScale 精确对齐目标尺寸
    # （768p 源片 1344x768 非严格 16:9，multiplier 产出 1890x1080 ≠ 1920x1080）
    _insert_exact_scale(workflow, target_w, target_h)

    return workflow


def _insert_exact_scale(workflow: dict, target_w: int, target_h: int) -> None:
    """在 CreateVideo 的 images 输入前插入 ImageScale 节点，确保输出严格匹配目标尺寸。

    查找 CreateVideo 节点，截断其 images 输入源，插入 ImageScale（lanczos + stretch）
    作为新的中间节点。仅当源节点不是 ImageScale 自身时插入（防重复注入）。
    """
    create_video = next(
        (n for n in workflow.values()
         if isinstance(n, dict) and n.get("class_type") == "CreateVideo"),
        None,
    )
    if create_video is None:
        return
    images_input = create_video["inputs"].get("images")
    if not (isinstance(images_input, list) and len(images_input) == 2):
        return
    src_nid, src_output = str(images_input[0]), images_input[1]
    # 已注入过则跳过
    src_node = workflow.get(src_nid)
    if src_node and isinstance(src_node, dict) and src_node.get("class_type") == "ImageScale":
        return
    # 分配新节点 ID
    nums = [int(k) for k in workflow if str(k).isdigit()]
    new_nid = str((max(nums) if nums else 9000) + 1)
    workflow[new_nid] = {
        "class_type": "ImageScale",
        "inputs": {
            "upscale_method": "lanczos",
            "width": target_w,
            "height": target_h,
            "crop": "disabled",
            "image": [src_nid, src_output],
        },
        "_meta": {"title": "ExactScale"},
    }
    create_video["inputs"]["images"] = [new_nid, 0]


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
        """上传输入文件（图片/视频/音频）到 ComfyUI 的 input 目录，返回其文件名。"""
        suffix = path.suffix.lower()
        if suffix in (".mp4", ".mov", ".webm", ".mkv"):
            mime = "video/mp4"
        elif suffix in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
            mime = "audio/mpeg"
        else:
            mime = "image/png"
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

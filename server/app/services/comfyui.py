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
MEGAPIXELS_768P = 0.98  # 2026-09-28 拍板：0.98 百万像素 = H3 原生 768p 画布（16:9 即 1344x768，避开 1.0 档超面积上限）

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
    "4k": {
        "steps": 1,
        "short_side": 2160,   # 3840×2160 等比，~2.8×
        "target_size": {"16:9": (3840, 2160), "9:16": (2160, 3840), "1:1": (2160, 2160)},
    },
}
# 768p 原生画布（0.98 百万像素）的各画幅尺寸，用于换算放大倍数
SOURCE_SIZES = {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (976, 976)}
# 7B 原生节点 SeedVR2VideoUpscaler.resolution 以 16:9 输出高度为基准；
# 9:16/1:1 竖屏时以宽为短边，需取长边值才能得到「短边=1080/1440」的等比输出（官方模板说明）
UPSCALE_7B_RESOLUTION = {
    "16:9": {"1k": 1080, "2k": 1440, "4k": 2160},
    "9:16": {"1k": 1920, "2k": 2560, "4k": 3840},
    "1:1": {"1k": 1080, "2k": 1440, "4k": 2160},
}
UPSCALE_FRAME_BATCH = 21       # 帧批（4n+1）
UPSCALE_TEMPORAL_OVERLAP = 3   # 时域重叠帧数


def load_workflow(mode: str) -> dict:
    # 电商广告片：视频阶段复用 r2v（ref2va）模板与权重族；广告图阶段另有独立模板
    # （server/workflows/advideo_image_api.json，见 services/advimage.py）
    if mode == "advideo":
        mode = "r2v"
    path = WORKFLOW_DIR / f"{mode}_api.json"
    if not path.exists():
        raise ComfyUIError(f"缺少工作流模板: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 长视频导演台
# TimelineDirector（Songssx）有限分段：director_api.json 为 13 节点扁平表，
# 段窗口语义见插件 docs/TIMELINE_SEGMENT_WINDOWS_CN.md：
#   - 段长 = endFrame - startFrame，必须吸附到 5 + 17n 帧（上限 3592 ≈150s@24fps）
#   - 段间交叠必须是 H3 合法 guide 帧数（0 / 1 / 5+17n），插件默认 39 帧（=5+17*2）
#   - 总帧数 = 末段 endFrame（不是各段长度直接相加）
DIRECTOR_OVERLAP_FRAMES = 39
DIRECTOR_MAX_SEGMENT_FRAMES = 3592
DIRECTOR_SECOND_PASS_MODEL = "minimax_h3_latent_upscaler_3d_fp16.safetensors"
# 已实测的导演台画布（16:9 / megapixels=1.0 / multiple=32）
DIRECTOR_SIZE = {"16:9": (1344, 768), "9:16": (768, 1344)}  # 2026-09-28：对齐 0.98MP 768p 原生画布


def align_h3_frames(requested_frames: float, fps: int = 24) -> int:
    """把期望帧数吸附到 H3 合法帧数 5 + 17n（与插件 _aligned_h3_length 同规则）。"""
    requested = max(5.0, float(requested_frames))
    n = max(0, round((requested - 5.0) / 17.0))
    return min(int(5 + 17 * n), DIRECTOR_MAX_SEGMENT_FRAMES)


def valid_overlap_frames(overlap: int) -> int:
    """交叠吸附：0 / 1 / 5+17n 三档（插件 _valid_guide_frames 规则）。"""
    overlap = int(overlap)
    if overlap <= 0:
        return 0
    if overlap == 1:
        return 1
    if overlap < 5:
        return 1
    n = max(0, round((overlap - 5.0) / 17.0))
    return int(5 + 17 * n)


def plan_director_segments(segments, aspect_ratio: str = "16:9", fps: int = 24,
                           overlap_frames: int = DIRECTOR_OVERLAP_FRAMES) -> dict:
    """把「每段时长(秒) + 提示词 + 参考图」排版为插件的段窗口。

    segments: [{"prompt": str, "duration": float, "ref_image_ids": [int]}]
    返回 {"segments":[{index,prompt,ref_image_ids,frames,start_frame,end_frame}],
          "total_frames": int, "width": int, "height": int, "fps": int, "overlap_frames": int}
    """
    if aspect_ratio not in DIRECTOR_SIZE:
        raise ValueError(f"导演台画布暂只支持 16:9 / 9:16（已实测 16:9 → 1376x768），收到 {aspect_ratio}")
    if not segments:
        raise ValueError("导演台至少需要 1 段")
    if len(segments) > 64:
        raise ValueError("导演台段数上限 64（插件限制）")
    overlap = valid_overlap_frames(overlap_frames)
    planned, cursor = [], 0
    for i, seg in enumerate(segments):
        # 段清单有两种来源：路由层传入的「原始段」（duration 秒）与
        # worker 从 tasks.segments 读回的「已排版段」（frames/start_frame/end_frame）。
        # 已排版段直接沿用 frames，保证重排幂等（否则 KeyError: duration）。
        if seg.get("frames"):
            frames = int(seg["frames"])
        else:
            frames = align_h3_frames(round(float(seg["duration"]) * fps), fps)
        if i == 0:
            start = 0
        else:
            start = cursor - overlap
            assert start > planned[-1]["start_frame"], "段起点必须前进"
        end = start + frames
        planned.append({
            "index": i,
            "prompt": str(seg["prompt"]),
            "ref_image_ids": list(seg.get("ref_image_ids") or []),
            "frames": frames,
            "start_frame": start,
            "end_frame": end,
        })
        cursor = end
    w, h = DIRECTOR_SIZE[aspect_ratio]
    return {
        "segments": planned,
        "total_frames": planned[-1]["end_frame"],
        "width": w, "height": h, "fps": fps, "overlap_frames": overlap,
    }


def build_director_timeline(plan: dict, images, global_prompt: str = "") -> str:
    """组装 TimelinePlanner 的 timeline_data（结构对齐已跑通实例的提交体）。"""
    fps = plan["fps"]
    seg0 = plan["segments"][0]
    timeline = {
        "version": 9,
        "fps": fps,
        "globalPrompt": global_prompt,
        "secondPass": False,
        "secondPassModel": DIRECTOR_SECOND_PASS_MODEL,
        "secondPassHighSteps": 2,
        "selection": {"start": 0, "duration": seg0["frames"] / fps},
        "videoAudioEnabled": True,
        "videoClips": [],
        "images": images,
        "audios": [],
        "segmentConfig": {
            "count": len(plan["segments"]),
            "mode": "timeline",
            "segments": [
                {
                    "images": [f"u{rid}" for rid in s["ref_image_ids"]],
                    "audios": [],
                    "prompt": s["prompt"],
                    "startFrame": s["start_frame"],
                    "endFrame": s["end_frame"],
                }
                for s in plan["segments"]
            ],
        },
    }
    return json.dumps(timeline, ensure_ascii=False)


def inject_director(workflow: dict, plan: dict, images, seed: int,
                    unet_name: Optional[str] = None) -> dict:
    """把段窗口/画布/权重/种子注入 director_api.json（就地修改并返回）。

    - UNETLoader：unet_name（导演台用 Singularity v1.3 int8，由 worker 的
      pool.unet_for("director") 取 unet:director:<文件> 标签传入）
    - MiniMaxH3TimelinePlanner：width/height/generation_seconds(=总帧数/24)/timeline_data
    - MiniMaxH3FiniteSegmentSampler：seed（各段由插件按该基种子派生）
    其余节点（SigmaShift shift12/3、ref2v turbo LoRA、Sol-Attn 0.4/tau1.0、euler、8 步）沿用模板。
    """
    fps = plan["fps"]
    timeline_data = build_director_timeline(plan, images)
    hit = []
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        ct = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if ct == "UNETLoader" and unet_name:
            inputs["unet_name"] = unet_name
            hit.append("unet")
        elif ct == "MiniMaxH3TimelinePlanner":
            inputs["width"] = plan["width"]
            inputs["height"] = plan["height"]
            inputs["generation_seconds"] = plan["total_frames"] / fps
            inputs["timeline_data"] = timeline_data
            hit.append("planner")
        elif ct == "MiniMaxH3FiniteSegmentSampler":
            inputs["seed"] = int(seed)
            hit.append("sampler")
    missing = {"unet", "planner", "sampler"} - set(hit)
    if missing:
        raise ComfyUIError(f"director_api.json 缺少节点: {sorted(missing)}")
    return workflow


# ---- 注意力后端门控（Sol-Attn）----
# 生成模板统一带 Sol-Attn 节点（BlockSparseAttention，节点 9600）；该节点仅
# 0.35.0 + comfy-kitchen>=0.2.33 提供。目标节点（如 0.34 的 4090）没有时，
# 提交前从「本次提交的副本」里摘除并把下游引用接回上游，磁盘模板保持不变。
SOL_ATTN_CLASSES = {"BlockSparseAttention"}
SOL_ATTN_TITLE_HINTS = ("Sol-Attn", "Sparse Attention")
_CAP_CACHE: dict = {}
_CAP_TTL = 300.0


async def _node_supports_class(base_url: str, class_type: str) -> bool:
    """探测目标 ComfyUI 是否注册了 class_type（进程内缓存 TTL；探测失败按“支持”处理，避免误摘）。"""
    import time

    key = (base_url.rstrip("/"), class_type)
    now = time.time()
    cached = _CAP_CACHE.get(key)
    if cached and now - cached[0] < _CAP_TTL:
        return cached[1]
    ok = True
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("%s/object_info/%s" % (key[0], class_type))
            resp.raise_for_status()
            data = resp.json()
        ok = bool(isinstance(data, dict) and data.get(class_type))
    except Exception as exc:  # noqa: BLE001
        logger.warning("注意力节点能力探测失败(%s %s): %s；按支持处理", key[0], class_type, exc)
        ok = True
    _CAP_CACHE[key] = (now, ok)
    return ok


async def gate_attention_nodes(workflow: dict, base_url: str) -> dict:
    """【2026-09-20 起已停用，保留备查/回滚】目标节点不具备 Sol-Attn 能力时摘除该节点，重连上游。

    停用原因：51/246 两个 4090 生成节点升级到 ComfyUI 0.36.0 + comfy-kitchen 0.2.35 后，
    全部生成节点均注册 BlockSparseAttention(9600)，不再需要在提交前做能力探测与摘除。
    worker.py 中的调用已注释；如需回滚见该处注释。

    原地修改并返回同一 dict；找不到安全重连点时原样保留，绝不阻断提交。
    """
    for nid, node in list(workflow.items()):
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        title = (node.get("_meta") or {}).get("title") or ""
        if class_type not in SOL_ATTN_CLASSES and not any(h in title for h in SOL_ATTN_TITLE_HINTS):
            continue
        if class_type and await _node_supports_class(base_url, class_type):
            continue
        src = (node.get("inputs") or {}).get("model")
        if not (isinstance(src, list) and len(src) == 2):
            logger.warning("节点 %s(%s) 无 model 输入，无法安全摘除，保持原样", nid, class_type)
            continue
        upstream, out_idx = src[0], src[1]
        rewired = 0
        for other_id, other in workflow.items():
            if other_id == nid or not isinstance(other, dict):
                continue
            inputs = other.get("inputs")
            if not isinstance(inputs, dict):
                continue
            for slot, value in inputs.items():
                if (isinstance(value, list) and len(value) == 2
                        and str(value[0]) == str(nid) and value[1] == out_idx):
                    inputs[slot] = [upstream, out_idx]
                    rewired += 1
        del workflow[nid]
        logger.info("[gate] 节点 %s(%s) 目标节点不支持，已摘除并重连 %s 处引用 → 上游 %s:%s",
                    nid, class_type, rewired, upstream, out_idx)
    return workflow


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
    # 2026-10-08 修复：advideo 视频阶段复用 r2v 模板（load_workflow 内部已重映射，
    # 但 inject 未重映射），导致 advideo 不进 r2v 重建分支，模板 demo 参考图
    # (red_superboy.../mecha_dragon...) 残留 → ComfyUI 报 node 139 Invalid image file。
    if mode == "advideo":
        mode = "r2v"
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
    src_abs_path: Optional[str] = None,
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
        # ---- VOSR2 级联（VOSR2 1x 缓存节点 → RTX VSR）----
        elif class_type == "VOSR2ModelLoader":
            # model/dtype 必须取 205 /object_info 实查的 combo 值
            inputs["model"] = "VOSR2"
            inputs["dtype"] = "fp16"
        elif class_type == "VOSR2UpscaleCached":
            # L2 磁盘缓存节点：以下所有参与 key_payload 的参数必须跨档位完全一致，
            # 否则 2K 与 4K 永不共享缓存键（seed 尤其禁止 random）
            inputs["upscale"] = 1
            inputs["seed"] = 42                       # 固定常量
            inputs["color_alignment"] = "wavelet"
            inputs["tile_size"] = 1024
            inputs["tile_overlap"] = 32
            inputs["vae_tile_size"] = 1024
            inputs["vae_tile_overlap"] = 32
            # 源片在超分节点本地的绝对路径（缺失/为空时节点自动回退 tensor 哈希）
            inputs["source_path"] = src_abs_path or ""
            # 固定常量：严禁把 tier/分辨率塞进来，否则键跨档不同、永不命中
            inputs["source_extra"] = '{"src":"minimax-ui"}'
            inputs["cache_dir"] = inputs.get("cache_dir") or "/data/ComfyUI/cache_vosr2"
            inputs["model_tag"] = inputs.get("model_tag") or "VOSR2:fp16"
            inputs["use_cache"] = True
        # ---- RTX Video Super Resolution 管线 ----
        elif class_type == "RTXVideoSuperResolution":
            # RTX 节点 resize_type 是 DynamicCombo（ComfyUI 0.34 comfy_api.latest）：
            # 提交格式 = 选中项 key 字符串 + 嵌套字段点号平铺（resize_type.width / .height）
            # ComfyUI 内部 build_nested_inputs 会把平铺字段重组为 UpscaleTypedDict 传给 execute()
            # 若塞整个 dict，live_inputs 匹配不到 option key，execute() 会报 missing resize_type
            inputs["resize_type"] = "target dimensions"
            inputs["resize_type.width"] = target_w
            inputs["resize_type.height"] = target_h
            inputs["quality"] = "ULTRA"

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
    # 2026-09-17 SAI：RTX VSR 的 target dimensions 分支已直接输出目标尺寸（节点内部对齐 8 的倍数），
    # FlashVSR 链路（2x → RTX）下再插 ImageScale 只是同尺寸重算，4K/124 帧实测白耗 84.6s，故跳过。
    if src_node and isinstance(src_node, dict) and src_node.get("class_type") in ("ImageScale", "RTXVideoSuperResolution"):
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

    async def wait_result(self, prompt_id: str, timeout_seconds: Optional[float] = None) -> dict:
        """轮询 /history/{prompt_id}，完成后返回产物文件信息
        {"filename": ..., "subfolder": ..., "type": ...}。

        timeout_seconds 覆盖默认 comfyui_timeout_minutes（长视频导演台单次可达数十分钟）。
        """
        total_seconds = float(timeout_seconds) if timeout_seconds else settings.comfyui_timeout_minutes * 60
        deadline_loops = int(total_seconds / settings.comfyui_poll_interval)
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

    async def wait_image_result(self, prompt_id: str) -> dict:
        """等待出图任务完成，返回首个图片产物文件信息。

        与 wait_result 的区别：不过滤视频后缀（SaveImageAdvanced 产物在 outputs.images），
        超时用 settings.advideo_image_timeout_minutes（含 PE 冷启 195-285s 余量）。
        """
        timeout_min = settings.advideo_image_timeout_minutes
        poll = max(1.0, float(settings.comfyui_poll_interval))
        max_polls = max(1, int(timeout_min * 60 / poll))
        async with httpx.AsyncClient(timeout=30) as client:
            for _ in range(max_polls):
                await asyncio.sleep(poll)
                r = await client.get(f"{self.base}/history/{prompt_id}")
                if r.status_code != 200:
                    continue
                entry = (r.json() or {}).get(prompt_id)
                if not entry:
                    continue
                status_info = entry.get("status") or {}
                if status_info.get("status_str") == "error":
                    raise ComfyUIError(f"ComfyUI 执行出错: {status_info.get('messages', [])}")
                if not status_info.get("completed"):
                    continue
                for outputs in entry.get("outputs", {}).values():
                    for f in outputs.get("images") or []:
                        if isinstance(f, dict) and f.get("filename", "").lower().endswith(
                            (".png", ".jpg", ".jpeg", ".webp")
                        ):
                            return f
                raise ComfyUIError("任务完成但未找到图片产物，请检查 SaveImageAdvanced 输出节点")
        raise ComfyUIError(f"ComfyUI 出图超时（{timeout_min} 分钟）")

    async def fetch_file(self, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.get(
                f"{self.base}/view",
                params={"filename": filename, "subfolder": subfolder, "type": folder_type},
            )
        if r.status_code != 200:
            raise ComfyUIError(f"下载 ComfyUI 产物失败: {filename}")
        return r.content

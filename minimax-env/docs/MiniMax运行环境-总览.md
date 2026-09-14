# MiniMax H3 运行环境 · 总览

> 采集时间 2026-09-14（快照文件见各 `nodeXX/env.txt`）；本页为三生成节点 + 一超分节点的版本/角色/参数汇总。

## 1. 集群拓扑

| 节点 | IP / 主机名 | 角色 | GPU | ComfyUI 端口 | 启动参数（当前线上） |
|---|---|---|---|---|---|
| **45** | 192.168.10.45 / gpu05 | 生成（heavy，A100）+ 超分管线 | A100-SXM4-40GB | 8188 | `--listen 0.0.0.0 --port 8188 --use-sage-attention --vram-headroom 8 --fp16-vae` |
| **51** | 192.168.10.51 / gpu51 | 生成（4090） | RTX 4090 24G | 8188 | `--listen 0.0.0.0 --port 8188 --use-sage-attention --vram-headroom 2` |
| **246** | 192.168.10.246 / gpu01server | 生成（4090）+ 超分（3060） | RTX 4090 24G + RTX 3060 12G | 8188（生成）/ 8189（超分） | 8188：`--use-sage-attention --vram-headroom 2`；8189：`--use-sage-attention --database-url sqlite:////data/ComfyUI/user/comfyui_8189.db` |
| **205** | 192.168.5.205 / 512g-3060 | 超分（3090，VOSR2+RTX VSR） | RTX 3090 24G | 8188 | `--listen 0.0.0.0 --port 8188 --fp16-vae`（`CUDA_VISIBLE_DEVICES=0`） |

> 说明：工单背景提到「45 另有超分 8189/8190」，实测当次快照 45 仅 8188 在监听，未发现 8189/8190 启动脚本（如需请补采）。

## 2. 软件版本

| 项 | 45 | 51 | 246 | 205 |
|---|---|---|---|---|
| OS | Ubuntu 22.04.5 | (Debian/Ubuntu) | Ubuntu 24.04 | Debian 12 |
| NVIDIA 驱动 | 580.65.06 | 595.84 | 595.84 | 580.105.08 |
| ComfyUI | v0.34.0-20-gd3eaf6ad (`d3eaf6ad`) | v0.34.0-28-gace9172e (`ace9172e`) | v0.34.0-28-gace9172e (`ace9172e`) | 0.34.0（**非 git 部署**） |
| Python | 3.10.12 | 3.12.3 | 3.12.3 | 3.11.2 |
| PyTorch / CUDA / cuDNN | 2.13.0+cu130 / 13.0 / 92000 | 2.13.0+cu130 / 13.0 / 92000 | 2.13.0+cu130 / 13.0 / 92400 | 2.13.0+cu130 / 13.0 / 92000 |

## 3. custom_nodes（含 commit）

| 节点 | ComfyUI-MiniMax-H3-PDD-Acc | Nvidia_RTX_Nodes | VideoHelperSuite | VOSR2 / VOSR2Cache | NvencSave |
|---|---|---|---|---|---|
| 45 | 311a65d | 892515e | 4d907be | — | — |
| 51 | 311a65d | 892515e | — | — | — |
| 246 | 311a65d | 892515e | — | — | — |
| 205 | — | 892515e | 4d907be | VOSR2 `c9450b6` / VOSR2Cache | 有 |

## 4. 模型清单（按节点）

| 用途 | 45 | 51 | 246 | 205 |
|---|---|---|---|---|
| 生成 DiT | FL2VA/Ref2VA `pruned_int8_convrot`（20G） | FL2VA/Ref2VA `pruned_fp8_scaled` + `int8_convrot` | 同 51 | — |
| 文本编码器 | `qwen3vl_32b_minimax_h3_int8_convrot` + `nvfp4_awq` | `qwen3vl_32b_minimax_h3_nvfp4_awq` | `qwen3vl_32b_minimax_h3_nvfp4_awq` | — |
| VAE | h3 video VAE fp16 / audio VAE fp32 + seedvr2 VAE | h3 VAE | h3 VAE + seedvr2 VAE | seedvr2_ema_vae_fp16 |
| 超分 DiT | seedvr2 3b_fp16 / 7b_fp8 / 7b_int8 | — | seedvr2 3b_fp16 / 7b_int8 | seedvr2 3b_fp16 / 3b_int8 / 7b_fp8 / 7b_int8 |
| LoRA | fl2v/ref2v turbo 8step v1.0 & ref2v 4step v0.1 | 同 | 同 | fl2v 8step / ref2v 4step |

> 详细文件列表与真实大小见各节点 `env.txt` 的 `MODELS TREE` / `MODEL REAL SIZES` 段。

## 5. 关键运行参数（线上）

- **生成**：768p 原生；turbo LoRA 8step（FL2V）/4step（Ref2V）；sage attention；A100 侧 `--vram-headroom 8`（显存余量）。
- **超分**：
  - 45 / 246 走 `SeedVR2` 原生节点模板（`upscale_7b` / `upscale_3090`）。
  - 205 走 **VOSR2 1× + RTX VSR 级联**（`upscale_rtx` 模板，VOSR2 L2 磁盘缓存），本地 H.264 走 **NVENC** 硬编（`h264_nvenc`）。
- **UI/后端**：minimax-ui 后端 `uvicorn app.main:app --port 8001`（cwd=`server`），本 patch 见 `patches/`。

## 6. 本次运行环境变更（相对 origin/main）

1. minimax-ui 新增 **4K** 分辨率档（UI/后端/计费/模板注入全链路）：见 `docs/minimax-ui-本地修改说明.md`。
2. 生成模板改用 **euler 采样 + 固定 8step turbo LoRA**（去掉运行时 If/Else 切换）。
3. 新增超分模板 `upscale_rtx_api.json`（VOSR2 1× 缓存 + RTX VSR），worker 新增 `engine=rtx` 映射与 `comfyui_input_dir` 配置。
4. 205 节点 ComfyUI 源码 `video_types.py` 改用 NVENC 硬编（非 git 部署，改动以片段记录于 `node205/video_types_nvenc.snippet.txt`）。

# MiniMax H3 视频生成服务部署方案
**方案选型：ComfyUI + INT8 量化 · Linux 部署 · 本地+云端混合流水线**

> 目标：搭建类 Seedance 的视频生成服务。底层使用 MiniMax-H3（开源权重），本地完成 768p 视频生成，云端 API 完成上下文增强（H3-Context-IR）与 2K 重生成（H3-Regenerate-2K）。
>
> 目标机器：Linux · A100-40GB ×1 · 503GB RAM · NVMe 剩余 1.5TB
>
> 版本口径说明：文中文件名/节点名来自官方与社区教程（2026-09），实施时请以模型卡与官方教程当日版本为准。

---

## 1. 总体架构

```
用户浏览器（类 Seedance 前端）
    │  REST + WebSocket/SSE
    ▼
API 服务（FastAPI） + Postgres（用户/任务/额度） + Redis（任务队列）
    │
    ▼
任务编排器（三段式流水线，每任务一个状态机）
    ① 云端 H3-Context-IR API ──── 增强 prompt（异步 task_id 轮询）
    ② 本地 ComfyUI worker ─────── INT8 权重，生成 768p 视频（含音频）
    ③ 云端 H3-Regenerate-2K ───── 上传 768p → 2K 成片 → 下载
    │
    ▼
对象存储（MinIO / S3）+ 签名 URL + CDN 分发
```

**关键决策回顾**

| 决策点 | 结论 | 理由 |
|---|---|---|
| 量化精度 | **INT8**（本机） | NVFP4 是 Blackwell 架构（SM100+）独占，A100（Ampere SM80）无原生支持 |
| 执行引擎 | **ComfyUI headless** | H3 官方教程覆盖 T2V / FLF2V / R2V 全模式；工作流迭代不改代码；单卡视频生成并发=1，SGLang 吞吐优势无法体现 |
| 加速 | **Sage Attention** | 官方教程实测生成速度约提升一倍 |
| 云端分工 | Context-IR 与 Regenerate-2K 均走云端 | 两个组件未开源，仅 API 提供 |

---

## 2. 系统环境准备（Linux）

### 2.1 基础要求

| 项 | 要求 | 验证命令 |
|---|---|---|
| 操作系统 | Ubuntu 22.04/24.04 LTS（或其他主流发行版） | `lsb_release -a` |
| NVIDIA 驱动 | ≥ 550（CUDA 12.x） | `nvidia-smi` |
| Python | 3.11 或 3.12 | `python3 --version` |
| 磁盘 | 模型与工作区按第 8 节规划 | `df -h /data` |

### 2.2 创建运行用户与目录

```bash
sudo useradd -m -s /bin/bash comfy
sudo mkdir -p /opt/ComfyUI /data/models /data/hf-cache /data/staging /data/output
sudo chown -R comfy:comfy /opt/ComfyUI /data
```

### 2.3 安装系统依赖

```bash
sudo apt update
sudo apt install -y git python3.11 python3.11-venv python3-pip ffmpeg build-essential
```

### 2.4 HuggingFace 下载工具

```bash
sudo -u comfy python3.11 -m venv /opt/ComfyUI/venv
sudo -u comfy /opt/ComfyUI/venv/bin/pip install -U huggingface_hub
# 国内网络如需镜像：
# export HF_ENDPOINT=https://hf-mirror.com
```

---

## 3. ComfyUI 安装

```bash
sudo -u comfy bash -c '
  git clone https://github.com/comfyanonymous/ComfyUI /opt/ComfyUI
  cd /opt/ComfyUI
  git checkout $(git describe --tags $(git rev-list --tags --max-count=1))  # 锁定 ≥0.30.0 的稳定版
  /opt/ComfyUI/venv/bin/pip install -r requirements.txt
'
```

**安装自定义节点**（H3 工作流必需）：

```bash
cd /opt/ComfyUI/custom_nodes
git clone https://github.com/kijai/ComfyUI-KJNodes   # 提供 Patch Sage Attention KJ 等
# 按官方教程补齐 MiniMax H3 相关节点（原生节点或教程指定的自定义节点包）
cd /opt/ComfyUI && /opt/ComfyUI/venv/bin/pip install -r custom_nodes/ComfyUI-KJNodes/requirements.txt
```

> 版本硬要求：**ComfyUI ≥ 0.30.0**，否则 H3 节点不可用。

---

## 4. 模型下载与放置（INT8）

### 4.1 组件清单

| 组件 | 文件（INT8 示例） | 存放路径 | 说明 |
|---|---|---|---|
| 视频 DiT | `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `models/diffusion_models/` | 核心生成模型，convrot 为保质量量化变体 |
| 文本/视觉编码器 | Qwen3VL 系列（优先选 INT8/量化版） | `models/text_encoders/` | 体积大，量化或靠阶段式换入换出 |
| 视频 VAE / 音频 VAE | 官方发布文件 | `models/vae/` | T2V/I2V/R2V 解码 + 立体声音频 |
| LoRA（可选） | — | `models/loras/` | 按需 |

> 官方仓库 `MiniMaxAI/MiniMax-H3` 为 BF16 原件；INT8 量化来自社区（如 [Abiray 的 INT4/INT8/NVFP4 合集](https://ai.atomgit.com/hf_mirrors/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot)）。**上线前必须做第 10 节的质量评估**。

### 4.2 下载命令

```bash
sudo -u comfy bash -c '
  export HF_HOME=/data/hf-cache
  # 社区 INT8 仓库示例（以实际选定的仓库为准）：
  hf download Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot \
    --include "*int8*" --local-dir /data/models/h3-int8
'

# 校验后按 ComfyUI 目录约定软链（避免重复占用磁盘）：
sudo -u comfy ln -s /data/models/h3-int8/minimax_h3_fl2va_pruned_int8_convrot.safetensors \
  /opt/ComfyUI/models/diffusion_models/
# text_encoders / vae 同理
```

### 4.3 下载注意事项

- 下载峰值：缓存 + 正式目录会短暂双份占用，先确认 `/data` 空闲 > 2× 模型体积，完成后清理 `hf-cache`。
- 每个文件下载后核对模型卡给出的文件大小/哈希，防止传输截断。
- NVFP4 权重（约 12.5GB）可一并下载存档至 `/data/models/h3-nvfp4`，**不在 A100 上使用**，留给未来 Blackwell 节点。

---

## 5. 启动服务

### 5.1 手动启动（调试期）

```bash
cd /opt/ComfyUI
/opt/ComfyUI/venv/bin/python main.py \
  --listen 127.0.0.1 --port 8188 \
  --use-sage-attention
```

- `--listen 127.0.0.1`：ComfyUI **没有鉴权**，严禁直接暴露公网，仅允许本机编排器访问；如需远程调试改内网地址并配防火墙。
- `--use-sage-attention`：官方教程实测速度约翻倍；也可在工作流中用 `Patch Sage Attention KJ` 节点替代。
- 若出现 OOM，按顺序降级：先缩 `steps`/分辨率 → 加 `--lowvram` → 最后 `--novram`（显著变慢）。

### 5.2 systemd 常驻（生产）

`/etc/systemd/system/comfyui-h3.service`：

```ini
[Unit]
Description=ComfyUI MiniMax H3 worker (INT8)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=comfy
WorkingDirectory=/opt/ComfyUI
Environment=HF_HOME=/data/hf-cache
ExecStart=/opt/ComfyUI/venv/bin/python main.py --listen 127.0.0.1 --port 8188 --use-sage-attention
Restart=on-failure
RestartSec=10
# 大内存机器上允许 ComfyUI 充分利用内存做模型换入换出
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now comfyui-h3
journalctl -u comfyui-h3 -f   # 观察启动日志，直到出现 "To see the GUI go to"
```

### 5.3 显存预算（A100-40GB）

| 阶段 | 显存占用（估算，以实测为准） |
|---|---|
| DiT INT8 去噪（20 步默认；Turbo 4–8 步） | 主体开销 |
| 文本编码器 | ComfyUI 阶段式换入换出，不与 DiT 同时驻留 |
| VAE 解码 + 音频合成 | 尾部峰值 |

官方教程建议 24GB 显存即可跑 INT8 版，A100-40GB 有充足余量；503GB 内存为模型换入换出和长片段生成提供保障。

---

## 6. 本地验证清单

1. 浏览器访问 `http://<机器>:8188`（经内网/SSH 端口转发），确认 UI 正常加载。
2. 加载官方教程的 **T2V 工作流**，输入简短提示词，5 秒 768p 生成一次。
3. 生成期间 `nvidia-smi -l 1` 观察：显存峰值 < 38GB、无 OOM、GPU 利用率正常。
4. 依次验证 **FLF2V（首尾帧）** 与 **R2V（参考图）** 工作流各一次。
5. 确认产物包含**立体声音轨**（ffprobe 检查音频流）。
6. 记录单次生成耗时（基线，用于容量规划）。

---

## 7. 云端 API 接入（H3-Context-IR / H3-Regenerate-2K）

在 MiniMax 开放平台创建 API Key，写入编排器环境变量 `MINIMAX_API_KEY`（不入代码库）。

| 步骤 | 接口 | 方式 | 结果获取 |
|---|---|---|---|
| ① 上下文增强 | `POST /v2/h3_context_ir`，body：`model=H3-Context-IR`、`content`（原始脚本）、`duration`、`ratio` | 异步 | 轮询 `task_id`，增强结果在 `content.prompt` |
| ③ 2K 重生成 | Regeneration 接口：提交本地 768p 视频（编码/分辨率先经 ffmpeg 归一化） | 异步 | 轮询 `task_id` → 下载 2K mp4 |

统一要求：指数退避重试（≤3 次）、单任务超时上限（①10 分钟 / ③30 分钟）、失败保留 768p 中间产物。

---

## 8. 服务层集成（类 Seedance 产品层）

### 8.1 编排器与 ComfyUI 的对接（API 模式）

```bash
# 提交工作流（prompt_api_json = 工作流的 API 格式导出，注入增强后的 prompt 与输入图）
curl -X POST http://127.0.0.1:8188/prompt \
  -H 'Content-Type: application/json' \
  -d '{"prompt": <prompt_api_json>, "client_id": "worker-1"}'

# 轮询结果
curl http://127.0.0.1:8188/history/<prompt_id>
```

### 8.2 任务状态机

```
queued → context_ir → generating_768p → upscaling_2k → done
                    ↘ failed（可从断点重提，额度退还）
```

- 本地 GPU 任务**串行**（信号量=1）；云端两个 API 可多任务并发。
- 每步落盘中间产物 + 记录云端 `task_id`，实现断点续跑。
- 额度在进入 `generating_768p` 时扣除，失败退还。

### 8.3 组件选型

| 组件 | 选型 |
|---|---|
| API / 编排 | FastAPI（或 Go） |
| 队列 | Redis + RQ/Celery（按队列深度做削峰与扩容信号） |
| 元数据 | Postgres：users / tasks / credits |
| 产物存储 | MinIO 或对象存储 + 签名 URL + CDN |
| 降级通道 | 队列溢出时整任务路由官方云端 API，保住可用性 |

### 8.4 磁盘布局（1.5TB NVMe）

| 目录 | 内容 | 预算 |
|---|---|---|
| `/data/models/h3-int8` | 生产权重（DiT/编码器/VAE） | ~50–100GB（以实际为准） |
| `/data/models/h3-nvfp4` | NVFP4 存档（未来 Blackwell 节点用） | ~13GB |
| `/data/staging` | 768p 中间产物，保留 7 天 | 100GB |
| `/data/output` | 2K 成片，上传对象存储后清理 | 200GB |
| `/data/hf-cache` | 下载缓存，校验后清理 | 峰值 ~200GB |

定时任务：`find /data/staging -mtime +7 -delete` + output 上传后清理。

---

## 9. 运维

- **监控**：dcgm-exporter / nvidia-smi（显存、利用率、温度）；任务时长 P50/P95；队列深度；云端 API 失败率。
- **日志**：`journalctl -u comfyui-h3` 持久化；编排器日志含 `task_id` 全链路串联。
- **备份**：模型权重异地留档；任务库每日快照。
- **更新流程**：新权重先落 `/data/models` 影子目录 → 验证通过 → 切换软链 → 重启服务。

## 10. 上线验收清单

- [ ] T2V / FLF2V / R2V 三种模式各通过 10 条评估 prompt，与 BF16 对比画质无劣化（社区 INT8 无官方背书，此步不可省）
- [ ] 三段式流水线端到端跑通，2K 成片可下载
- [ ] 单任务失败可从断点恢复，额度正确退还
- [ ] ComfyUI 未暴露公网；API Key 无硬编码
- [ ] 确认 MiniMax-H3 license 允许商用
- [ ] 压测：连续 20 个任务无 OOM、无句柄泄漏，记录吞吐基线

## 11. 常见问题

| 现象 | 处置 |
|---|---|
| 启动即 OOM | `--lowvram` → `--novram`；检查是否有残留进程占卡 |
| 生成速度远低于基线 | 确认 `--use-sage-attention` 生效；检查是否被其他进程抢卡 |
| 云端任务长时间 pending | 查平台配额/并发限制；触发降级通道 |
| 产物无音轨 | 检查音频 VAE 组件是否齐全、工作流音频分支连接 |
| 下载中断/校验失败 | 重跑 `hf download`（支持断点续传），勿手工拼接文件 |

## 12. 扩展路径

1. **短期**：单卡 + 队列削峰 + 云端降级通道。
2. **中期**：增配 Blackwell 节点（B200 / 5090），启用已存档的 NVFP4 权重（约 12GB 显存/实例，单卡可跑 2–3 个 worker），按队列深度自动伸缩。
3. 架构无需改动：编排器按可用引擎分发，worker 池异构共存。

---

## 资料来源

- [ComfyUI 官方教程：MiniMax H3 T2V / I2V / R2V 本地工作流](https://docs.comfy.org/zh/tutorials/video/minimax/minimax-h3)
- [Comfy 官方：MiniMax H3 开源权重视频模型](https://comfy.org/zh-CN/minimax-h3/)
- [ComfyUI + MiniMax H3 实战教程（API 与本地）](https://news.qiniu.com/archives/1786351769428)
- [Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot 量化合集](https://ai.atomgit.com/hf_mirrors/Abiray/Minimax-H3-nvfp4-INT4-INT8-Convrot)
- [lilcheaty/MiniMax-H3-NVFP4（Blackwell 参考数据）](https://ai.gitcode.com/hf_mirrors/lilcheaty/MiniMax-H3-NVFP4)
- [NVIDIA NVFP4: Blackwell-exclusive 4-bit inference](https://neuralrack.ai/blog/nvidia-nvfp4-ultra-efficient-4-bit-llm-inference-exclusive-blackwell-gpus-feb-1-2026)
- [Create H3-Context-IR Task – MiniMax API Docs](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)
- [MiniMax-H3 Regeneration – APIMart Docs](https://docs.apimart.ai/ko/api-reference/videos/minimax-h3/regeneration)

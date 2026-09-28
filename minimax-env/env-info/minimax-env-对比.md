# 与 `/data/workspace/minimax-env` 目录的对比（2026-09-26）

## 1. 两个目录的关系（实测）
| 对象 | 位置 | commit | 日期 | remote | 工作区 |
|---|---|---|---|---|---|
| 独立 env 仓库 | 宿主机 45 `/data/workspace/minimax-env`（19 文件） | `6358c8f1b7e537fe5f42f3c4e847b767693f80ae` | 2026-09-14 11:10 | 无 remote | 干净（`git status --porcelain` 空） |
| 仓库内归档 | `origin/main:minimax-env/`（24 文件） | 随 minimax-ui 仓库，c54d32a 时点 | 2026-09-17 | 随主仓库 | — |

`origin/main:minimax-env/` 比独立仓库**多**这些文件：
- `env-info/00-环境变更说明.md`、`env-info/server.env.masked`、`env-info/nginx-minimax-ui.conf`、`env-info/comfyui-cloud-tunnel.service`
- `docs/minimax-ui-本地修改说明-20260917.md`
- `patches/minimax-ui-local-changes-20260917.patch`（2,014 行）
- `README.md` 有增补

其余 18 个文件（`docs/` 8 篇 + `node205/246/45/51` 快照 + `patches/minimax-ui-local-changes.patch` + `scripts/collect_node.sh`）两边同名。

## 2. 结论：env 目录需要刷新，缺 09-18 ~ 09-26 的变更
`minimax-env`（含仓库内归档）记录的环境时点是 **2026-09-14 / 09-17**，此后运行环境发生了以下变化，**归档里都没有**（本轮已把原始文件放进本包 `env-info/`，可直接补进该目录）：

| # | 变更 | 影响面 |
|---|---|---|
| 1 | `server/.env`：51/246 生成节点新增 `director` 能力标签与 `unet:director:Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8.safetensors`；新增 `DIRECTOR_ENABLED=true` | 生成池 / 长视频导演台 |
| 2 | nginx `/video/` alias 由 `/data/ComfyUI/output/video/` 改为 **`/data/minimax-ui/output/`**（09-18 重挂 + types 白名单） | 公网产物访问路径 |
| 3 | nginx 新增 `/fasth3v2/`、`/interguide/`、`/vpromot/` 三个只读静态挂载 | 公网报告页 |
| 4 | nginx 新增 `/qwen21/` 反代（127.0.0.1:8192）+ API Key 门禁 map（key 在独立文件 `qwen21-apikey.conf`，**不入包**）+ `absolute_redirect off` + `%2F` 保真修复 | qwen21 对外 API |
| 5 | nginx 新增 `/qwen21` → 301 `/qwen21/`（公网只暴露 80/443 的兼容入口） | 同上 |
| 6 | 后端进程改用仓库内 venv 解释器启动（`server/venv/bin/uvicorn`，PID 1516317，2026-09-24 19:14 起） | 运维/重启手法 |
| 7 | 51/246 生产 ComfyUI 0.36.0 均装 `BlockSparseAttention`（sol-attn 路线 B 去门控已生效） | 生成节点能力 |
| 8 | 45:8188 生成节点 `GET /system_stats` 返回 500（A100 驱动异常），与 09-17 归档时"可用"不同 | 45 节点状态 |
| 9 | 45:8192 新增 qwen-image 2.1 ComfyUI 实例在监听 | 新服务 |
| 10 | 本地仓库被 clone 为 shallow（无法 push） | 交付方式 |
| 11 | 本包 patch 内容（导演台后端/API + 三工作流对齐官方 + 删任务 500 修复等 14 文件） | 代码基线 |

**未复核项（如实标注）**：各节点模型文件清单、torch/CUDA 版本、启动脚本内容等，仍以 09-14 采集的 `node*/env.txt` 为准；本轮**未重新采集**节点快照（避免打扰生产节点）。如需刷新，跑 `minimax-env/scripts/collect_node.sh` 即可。

## 3. 建议
1. 把本包 `env-info/` 的 4 个文件（00-环境变更说明.md、server.env.masked、nginx-minimax-ui.conf、comfyui-cloud-tunnel.service）落到 `minimax-env/env-info/`，并补一份 `env-info/20260926/` 快照；
2. `minimax-env/patches/` 追加本包 `minimax-ui-local-changes-20260926.patch`；
3. 需要节点级细节时再跑一次 `collect_node.sh`（只读采集）。

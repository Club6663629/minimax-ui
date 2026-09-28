# minimax-ui 本地改动 patch — 2026-09-26

## 结论
本地 45 `/data/workspace/minimax-ui` 工作区相对 **GitHub origin/main** 有 **14 个文件**未入库改动（**+673 / −95**），
已打成单个 patch（`--binary`，含新增文件），并附整包 + 环境变更说明，挂公网可下载。公网实测 200、md5 与本地一致。

## 交付物
| 文件 | 大小 | md5 |
|---|---|---|
| minimax-ui-local-changes-20260926.patch | 51,558 B / 1,168 行 | 0e9bf93ddd3e036d66670e9d1f5d22fb |
| minimax-ui-patch-20260926.tar.gz | 见 md5sums.txt | — |
| web-dist-20260926.tar.gz | 前端构建产物（264K 源目录） | — |
| env-info/ | 环境变更说明 + 脱敏 server.env + nginx 配置 + systemd unit + 与 minimax-env 的对比 | — |

URL 前缀：**https://minimax.ai4ss.com/video/minimax-ui-patch-20260926/**

## 基线（重要）
- 基线 = **origin/main = c54d32a**
- 外部校验：GitHub API `GET /repos/Club6663629/minimax-ui/commits/main` → sha `c54d32af1df64263e33790486f1d94743f4d2953`，
  committer date `2026-09-17T09:15:11Z`，与本机 `git fetch origin` 后的 origin/main 一致 → 基线就是当前远端 HEAD。
- 本地仓库是 **shallow clone**（`git rev-parse --is-shallow-repository` → `true`，`.git/shallow` 存在），本地不能 push，
  与既有约定（本地无法提交、改动打包 patch 交用户）一致。
- 因此本 patch 的语义：**apply 到 origin/main 的全新工作区，即得到 45 上当前生产代码。**

## 应用方法
```bash
git clone https://github.com/Club6663629/minimax-ui.git
cd minimax-ui
git checkout c54d32af1df64263e33790486f1d94743f4d2953
git apply --binary minimax-ui-local-changes-20260926.patch
```
若远端 main 已前进：先 `git apply --3way`，或参照下方「排除项」人工处理冲突。

## 改动清单（14 文件，按功能分类）

### A. 长视频导演台（TimelineDirector）后端 / API —— 本轮主体
| 文件 | 变更 | 内容 |
|---|---|---|
| server/workflows/director_api.json | +159（新增） | 导演台工作流模板（13 节点扁平表） |
| server/app/services/comfyui.py | +172 | `align_h3_frames`（帧数吸附 5+17n）、`valid_overlap_frames`（交叠吸附 0/1/5+17n）、`plan_director_segments`（段窗口排版）、`build_director_timeline`、`inject_director`（段窗口/画布/权重/种子注入 director_api.json）、`wait_result`（带超时）；`gate_attention_nodes` 标注为 2026-09-20 起停用 |
| server/app/services/worker.py | +196 | `_director_pending/_director_busy/_claim_director/_dispatch_director/_run_director/_generate_director`、`_probe_image_size`（ffprobe 取参考图真实宽高）；导演台**全局单槽串行**（跑导演台期间不领短视频任务） |
| server/app/services/pool.py | +26 | `has_director` / `acquire_director`（只认 `director` 标签且完全空闲节点，独占整卡、无兜底派发）；权重标签支持 `unet:<mode>:<file>`，使导演台（Singularity）与短视频（官方 convrot）不再共用族标签串味 |
| server/app/routers/videos.py | +58 | `_create_director` 分支 |
| server/app/schemas.py | +19 | `mode` 增加 `director`；`DirectorSegmentIn`（每段 prompt/duration/ref_image_ids，段数 ≤64）；`TaskOut.segments` |
| server/app/models.py | +5 | `Task.segments`（JSON 段清单） |
| server/app/routers/serialize.py | +6 | 回传 segments（已吸附的 frames/start/end/seed 供核对） |
| server/app/services/billing.py | +11 | `compute_director_cost`（按 5s 档单价 × ceil(总秒/5)） |
| server/app/config.py | +2 | `director_enabled`（默认 False） |

### B. 三个短视频工作流对齐官方（VAE / 采样器）+ sol-attn 参数
`server/workflows/t2v_api.json`、`r2v_api.json`、`flf2v_api.json`：
- 视频 VAE：`minimax_h3_video_vae_fp16.safetensors` → **`minimax_h3_video_vae_int8_convrot.safetensors`**（对齐官方）
- 采样器：`euler` → **`res_multistep`**（对齐官方）
- sol-attn：`start_percent` 0.2→**0.4**、`selection.tau` 1.3→**1.0**、`shift_video` 6.0→**12.0**、`megapixels` 0.98→**1.0**、`verbose` true→false
- 删除冗余 `PrimitiveInt` 节点 9019/9020（值 20 / 8）

### C. 杂项
- `.gitignore`（+1/−1）：去掉 `!minimax-env/patches/*.patch` 例外行

## 三条自检（原始输出）
1. `git apply --check -R <patch>`（对本地工作区）→ **REVERSE-OK** —— 说明 patch 就是工作区实际改动，可反向干净回退
2. 干净基线导出（`git archive origin/main | tar -x -C /tmp/vfy26`，非仓库树）：
   `git apply --binary --check` → **APPLY-CHECK-OK**；实际 `git apply --binary` → **APPLIED-OK**
3. 逐文件哈希比对（patch 内 14 文件，本机 vs 应用后）：`compared=14 mismatch=0` → **TREE-MATCH-OK**

## 排除项及原因
| 排除 | 原因 |
|---|---|
| `minimax-env/`（24 文件） | 远端有、本地工作区无（该目录在 /data/workspace/minimax-env 独立仓库维护）；不排除会变成"删除 24 个文件" |
| `H3集群部署方案.md` | 远端有、本地无（非本地改动），同理排除 |
| `*.bak.*` / `*.bak_*` 等 100+ | 历史备份 |
| `tmp_ci/`、`patch_backend.py`、`patch_frontend.py` | 一次性脚本 / 中间产物 |
| `server/.env`、`server/clouds/autodl-*.env` | 含密钥，不入包；`server/.env` 另给脱敏副本 `env-info/server.env.masked` |
| `web/node_modules`、`server/venv`、`__pycache__`、日志 | 体积大 / 可再生 |
| `web/dist`（264K） | 被 .gitignore 排除，另打 `web-dist-20260926.tar.gz` |

## 环境侧（不在仓库，见 env-info/）
- `env-info/00-环境变更说明.md`：当前运行环境 vs 2026-09-17 归档的逐项差异（含原始命令输出）
- `env-info/minimax-env-对比.md`：与 `/data/workspace/minimax-env` 目录的对比结论
- `env-info/server.env.masked`、`env-info/nginx-minimax-ui.conf`、`env-info/comfyui-cloud-tunnel.service`、`env-info/diff-*.txt`：原文与 diff

## 清理 / 回滚
`rm -rf /data/minimax-ui/output/minimax-ui-patch-20260926`（只删本次挂载目录，不影响服务与其它文件）

> 注：`md5sums.txt` 由发布时对目录内容计算，仅存在于发布目录（不在 tar 包内）。

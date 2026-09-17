# minimax-ui 本地改动 patch 包（2026-09-17）

> 目的：把**宿主机 45 `/data/workspace/minimax-ui` 工作区里"远端 GitHub 还没有的"全部本地改动**打包，供你自行提交。
> 本地无法 push GitHub，本包**只生成 patch，不做任何 push**。

## 一、包内文件

| 文件 | 说明 |
|---|---|
| `minimax-ui-local-changes-20260917.patch` | **主 patch**：23 个文件、可 `git apply`；已排除远端已有的 `minimax-env/` |
| `env-info/00-环境变更说明.md` | 仓库**之外**的本地环境改动（后端启动、systemd 隧道、nginx、.env 配置项） |
| `env-info/server.env.masked` | `server/.env` 脱敏副本（密钥已打码；**真实文件不入 patch**） |
| `env-info/comfyui-cloud-tunnel.service` | gpu05 上 systemd 隧道单元（云端节点 8183 反代） |
| `env-info/nginx-minimax-ui.conf` | gpu05 上 nginx 站点配置（`/video/` 交付目录 alias 等） |
| `web-dist-20260917.tar.gz` | 前端构建产物（`web/dist`，264 KB）；patch 里不含 dist，需要部署静态页时用它或自行 `npm run build` |
| `README.md` | 本文件 |

## 二、基线（baseline）与生成方式

- 仓库：`/data/workspace/minimax-ui`，remote `https://github.com/Club6663629/minimax-ui.git`
- 本次**先 `git fetch`**（实测 `=== fetch ===` 输出 `From https://github.com/Club6663629/minimax-ui → = [up to date] main -> origin/main`，rc=0，即远端已是最新）
- 远端最新 `origin/main` = **d8a834a**（2026-09-14 11:39, chenxj）「纳入 MiniMax H3 运行环境仓库」
- 本地工作区 `HEAD` = **ad3dd2b**，`git rev-list --left-right --count HEAD...origin/main` = **0 2**（落后 2 个提交）
  → 说明上次（09-14）给的 patch 已被你推上远端：`74c010c`（4K 超分链路 + 8step turbo LoRA + 超分RTX级联模板，15 文件）与 `d8a834a`（minimax-env 四节点环境快照）。
- 因此本次 patch 的基线取 **origin/main（d8a834a）**，`git diff origin/main`，只含"远端还没有的"改动（不重复已推内容）。

生成命令（用临时 `GIT_INDEX_FILE`，**没有污染本地 index**；未跟踪的新文件用 `git add -N` 纳入）：

```bash
cd /data/workspace/minimax-ui
export GIT_INDEX_FILE=/tmp/pidx.patch && rm -f $GIT_INDEX_FILE
git read-tree origin/main
git add -N server/app/services/clouds/__init__.py server/app/services/clouds/base.py \
           server/app/services/clouds/autodl.py server/app/services/clouds/registry.py \
           server/clouds/README.md server/clouds/example.env \
           deploy/autodl/README.md deploy/autodl/start_comfy.sh
git diff --binary -- . ":(exclude)minimax-env" > minimax-ui-local-changes-20260917.patch
```

**自检（均已真机执行通过）**

```bash
# 1) 对当前工作区反向自检：能干净反向应用 ⇒ patch 与工作区状态完全一致
git apply --check -R minimax-ui-local-changes-20260917.patch   → REVERSE-CHECK-OK
# 2) 对 origin/main 正向自检（临时索引）：能干净应用到远端最新 ⇒ 你可以直接 git apply
git apply --cached --check minimax-ui-local-changes-20260917.patch → APPLY-TO-ORIGINMAIN-OK
```

- **规模（实测 diffstat 汇总行）**：23 files changed, 1469 insertions(+), 149 deletions(-)；patch 文件 77,003 字节 / 2,014 行
- **patch MD5**：746c2755fbc372f38d150012bc58ab93（公网下载实测一致）

## 三、patch 内容清单（23 文件）

### A. 云端实例远程开关机（2026-09-15 新功能，本次主体）

| 文件 | +/- | 改动 |
|---|---|---|
| `server/app/services/clouds/__init__.py` | +8 | 新模块导出 |
| `server/app/services/clouds/base.py` | +117 | `CloudInstance`/`CloudProvider`/`CloudAPIError` 抽象（状态机 idle/starting/stopping、`controllable`、`last_op` 持久化失败原因） |
| `server/app/services/clouds/autodl.py` | +108 | AutoDL Provider：`Authorization: <token>`（不带 Bearer）、`/status`+`/snapshot` 用 GET+query、`/power_on` 需 `payload:"gpu"`、成败只看 body `code`（失败也返 HTTP 200）、snapshot 只取白名单字段 |
| `server/app/services/clouds/registry.py` | +256 | 实例注册/按 `CLOUD_WORKER_URL` 绑定节点、状态轮询 loop、开机/关机编排、开机后等就绪 |
| `server/app/config.py` | +4 | 新增 `clouds_dir` 配置项（留空 = `<server>/clouds`） |
| `server/app/schemas.py` | +50 | `CloudInfoOut` / `CloudPowerIn` / `CloudPowerOut`，Worker 出参新增 cloud 字段（`platform`/`instance_id`/`instance_status`/`op_state`/`power_controllable`/`last_op_*`） |
| `server/app/routers/admin.py` | +95/-1 | `GET /api/admin/clouds`（列表，绝不返 token）、`POST /api/admin/clouds/power`（开机/关机；只接受池中已知 url，uuid 由后端自查）+ 开关机联动 `systemctl start/stop comfyui-cloud-tunnel`（异常只记日志，不影响返回） |
| `server/app/services/pool.py` | +26 | 池启动时 `registry.bind_nodes()` 按 url 绑定云实例并起状态轮询；Worker `to_dict()` 补 cloud 字段 |
| `web/src/types.ts` | +26 | `WorkerInfo` 新增云字段 + `CloudPowerResult` |
| `web/src/api/client.ts` | +24/-6 | 解析后端**结构化错误**（`{ok,error_code,msg,request_id}`）→ `ApiError.errorCode`；新增 `adminCloudPower()` |
| `web/src/pages/AdminPage.tsx` | +209/-16 | Worker 池页：平台/实例/状态列 + 「开机」「关机」按钮（二次确认、busy 态、"开机中…/关机中…/运行中/已关机"徽标、失败短因 `NOT_FOUND/UPSTREAM_TIMEOUT/PLATFORM_UNSUPPORTED` 中文短词） |
| `server/clouds/README.md` | +28 | 实例 `.env` 字段说明（一台实例一个文件） |
| `server/clouds/example.env` | +15 | 模板（真实 `*.env` 已被 .gitignore 排除） |
| `deploy/autodl/README.md` | +53 | AutoDL 接入说明（一次性准备、控制面配置、隧道、接口） |
| `deploy/autodl/start_comfy.sh` | +24 | 云端开机自启脚本（幂等：按端口是否监听判定；实测启动到就绪约 94 s） |
| `.gitignore` | +4 | 排除 `server/clouds/*.env`（含 token），保留 `example.env` |

### B. Sol-Attn 注意力能力门控（2026-09-16 修复 4090 报错 `missing_node_type #9600`）

| 文件 | +/- | 改动 |
|---|---|---|
| `server/app/services/comfyui.py` | +73/-1 | 新增 `gate_attention_nodes()`：提交前探测目标节点 `/object_info/BlockSparseAttention`（进程内缓存 TTL 300s），不支持则从**本次提交的副本**里摘除 Sol-Attn 节点并把下游引用重连到上游（磁盘模板不动）；探测失败按"支持"处理，绝不阻断提交 |
| `server/app/services/worker.py` | +2 | 768P 生成提交前调用 `gate_attention_nodes(workflow, node.url)` |
| `server/workflows/flf2v_api.json` / `r2v_api.json` / `t2v_api.json` | 各 +23/-2 | 三个生成模板接入 `BlockSparseAttention`（节点 9600，`selection: sol-attn`, tau 1.3, start_percent 0.2, min_tokens 12288）；**4090（0.34）由后端自动摘除**，5090（0.35）原生走 Sol-Attn |

### C. 线上超分链路：VOSR2 → FlashVSR 2x + RTX VSR

| 文件 | +/- | 改动 |
|---|---|---|
| `server/workflows/upscale_rtx_api.json` | +237/-114 | 前级由 `VOSR2ModelLoader`/`VOSR2UpscaleCached` 换成 FlashVSR 链：`FlashVSRModelLoader(FlashVSR1_1.safetensors)` + `ModelAttentionBackend` + `FlashVSRLQLoader(LQ_proj_in)` + `FlashVSRPromptLoader` + `FlashVSRPrepareVideo(scale_multiplier=2.0)` + `FlashVSRApply` → `BasicGuider`/`RandomNoise`/`FlashVSRStreamingSampler`/`SamplerCustomAdvanced` → `FlashVSRTCDecoderLoader(TCDecoder-fp16, fuse_tgrow=true, channels_last=true, compile_memblocks=false)` + `FlashVSRTCDecode` + `FlashVSRCropFrames` → `RTXVideoSuperResolution`（放大到 2K/4K）→ `CreateVideo`→`SaveVideo`（NVENC，core H264→h264_nvenc 补丁） |
| `server/app/services/comfyui.py` | (同上) | `inject_upscale` 里 RTX VSR 之后**不再插 `ImageScale`**（VSR 已直接输出目标尺寸；4K/124 帧实测白耗 84.6 s） |

### D. 任务删除 500 修复（2026-09-16）

| 文件 | +/- | 改动 |
|---|---|---|
| `server/app/routers/videos.py` | +41/-5 | 删除任务递归收集子任务（`parent_task_id` 多级）→ 先把 `credit_logs.task_id` 置 NULL（`fk_2` 无 ON DELETE CASCADE，保留财务台账金额）→ 同事务删子任务与主任务；执行中任务 409 并列出具体任务号；异常回滚返回原因 |

## 四、明确排除的内容及理由

| 排除项 | 数量/体积 | 为什么排除 |
|---|---|---|
| `*.bak.*`、`*.bak_*`（`server/.env.bak.*`、`*.py.bak.*`、`*.json.bak.*`、`web/dist.bak.*` 等） | 71 项 | 每次改动前的时间戳备份，属过程产物，不构成交付 |
| `web/dist.bak.1789457050/`、`web/dist.bak.1789471854/` | 2 目录 | 旧构建产物备份（当前 dist 另打包为 `web-dist-20260917.tar.gz`） |
| `patch_backend.py` / `patch_frontend.py` | 567 行 | 2026-09-15 用的一次性"锚点替换 + 备份"落地脚本；其效果已完整体现在源码 diff 中，脚本本身无交付价值 |
| `server/*.log`、`uvicorn8001.log`、`uvicorn_8001.log.bak.*` | ~63 MB | 运行日志 |
| `server/venv/`、`web/node_modules/`、`__pycache__/` | — | 依赖/字节码，`.gitignore` 已排除，可重建 |
| `web/dist/` | 264 KB | 构建产物（patch 不含，单独 tar 包提供） |
| `server/.env` | — | **含 JWT_SECRET / DB 密码 / MiniMax API Key**，`.gitignore` 已排除；本包只给脱敏副本 |
| `server/clouds/autodl-7889ca37d10f.env` | — | **含 AutoDL API token**，`.gitignore` 已排除（仓库内 `server/clouds/README.md` 亦写明不要提交） |
| `minimax-env/` | 18 文件 | **远端 d8a834a 已有；本地工作区没有此目录**，不排除的话 patch 会表现为"删除 18 个文件"。用 `:(exclude)minimax-env` 剔除 |

> 说明：云端 4070Ti / 5070Ti 试验节点的 ComfyUI 侧改动（模型、FlashVSR 参数、venv）**不在 minimax-ui 仓库范围内**，不属本 patch；相关文档在 `/data/workspace/` 与 `/video/` 交付页。

## 五、应用方法（你这边）

```bash
git clone https://github.com/Club6663629/minimax-ui.git && cd minimax-ui   # 或直接用你现有仓库
git checkout main && git pull            # 确认到 d8a834a 或更新
git apply --check minimax-ui-local-changes-20260917.patch   # 先检查
git apply minimax-ui-local-changes-20260917.patch           # 应用
git add -A && git commit -m "云端实例开关机 + Sol-Attn 门控 + FlashVSR 超分链路 + 删任务500修复"
```

> 注意：`server/clouds/*.env`、`server/.env` 被 `.gitignore` 排除，提交后仍需在部署机各自维护（字段见 `env-info/00-环境变更说明.md`）。

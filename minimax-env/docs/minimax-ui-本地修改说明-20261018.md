# minimax-ui 本地改动 → 可合并补丁包（2026-10-18）

把 45 `gpu05:/data/workspace/minimax-ui` 工作区里**未提交、未推送**的改动，按主题拆成 3 个可独立审查的补丁，
交给下一个 agent 直接 `git am` 合并提交。

- 基线：`ed7dcabdafce498aae4c1f95854e7dc4ff5eea56`（合并时 `origin/main` 与本地 HEAD 同一提交，ahead/behind = 0/0）
- 远端：`https://github.com/Club6663629/minimax-ui.git`
- 结论：应用这 3 个补丁后，17 个文件与线上工作区**逐字节一致**，删除文件也一致（`verify.log` 第 4、5 节）

---

## 1. 产物

| 文件 | 大小 | 用途 |
|---|---|---|
| `am/0001-feat-advideo.patch` | 22,417 B | 推荐：`git am` 直接成提交（含作者/提交信息） |
| `am/0002-feat-worker.patch` | 26,435 B | 同上 |
| `am/0003-refactor-web.patch` | 13,563 B | 同上 |
| `01-advideo-auto-confirm.patch` | 20,490 B | 纯 diff，`git apply` 用 |
| `02-poison-guard.patch` | 24,876 B | 同上 |
| `03-web-remove-recharge.patch` | 12,407 B | 同上 |
| `00-all-in-one.patch` | 57,119 B | 一次应用全部（仅审阅/应急用，不便于分主题回滚） |
| `apply.sh` | — | 基线校验 + 顺序应用 / `--check` / `--rollback` |
| `verify.log` | 87 行 | 生成与验收全过程记录（sha1、逐文件 cmp、am 结果、密钥扫描） |

## 2. 三个补丁各做什么

### 01 — 广告片「自动模式」：候选图出完自动确认并进入合成
`+136 / -27`，11 个文件

| 文件 | 变更 |
|---|---|
| `server/app/services/advconfirm.py` | **新增（54 行）**：抽出“确认候选图”公共逻辑，人工接口与自动模式共用 |
| `server/app/routers/videos.py` | 确认接口改调 `advconfirm.apply_confirm`；创建/重生成支持 `auto_confirm`；状态接口下发 `auto_confirm_default` |
| `server/app/services/worker.py` | 出图完成后若自动模式，选 `ADVIDEO_AUTO_CONFIRM_INDEX` 指定候选图（越界吸附最后一张）→ 直接 `queued` |
| `server/app/config.py` / `models.py` / `schemas.py` / `serialize.py` / `main.py` | 新增 `advideo_auto_confirm`（默认 True）、`Task.ad_auto_confirm` 字段与启动迁移补列、序列化字段 |
| `web/src/pages/AdvideoPanel.tsx` / `api/client.ts` / `types.ts` | “自动模式”开关、按服务端默认初始化、文案区分自动/手动 |

> ⚠️ **默认行为有变**：合并部署后，广告片默认不再停在“等待人工确认”，出图即自动进入视频阶段并扣视频积分。
> 若不希望默认生效，部署环境设 `ADVIDEO_AUTO_CONFIRM=false`；单次可在前端开关切回手动。

### 02 — 毒丸任务隔离：长任务熔断 + 禁止跨节点重投 + 节点粘滞
`+420 / -7`，3 个文件

| 文件 | 变更 |
|---|---|
| `server/app/services/quarantine.py` | **新增（252 行）**：任务签名熔断名单、节点粘滞表、事件流；落盘 `data/poison_tasks.json`、`data/poison_events.jsonl` |
| `server/app/services/pool.py` | 节点记录宿主可用/总内存；长任务派发按内存准入过滤；新增 `acquire_url` 精确领取（粘滞用） |
| `server/app/services/worker.py` | 领取时命中熔断名单直接失败并退积分；节点失联的长任务不再回退重投；短任务只允许原节点重试，粘滞超限直接失败 |

> 背景：10-12~10-13 导演台长任务打爆宿主内存后被回退重投到其它节点，连锁打爆三台生成节点。
> 开关 `POISON_GUARD`（默认 1）；误熔断可改 `data/poison_tasks.json` 或调 `quarantine.clear_poison()`。

### 03 — 下线充值页，入口统一到积分明细
`+9 / -185`，6 个文件

删除 `web/src/pages/RechargePage.tsx`；`/recharge` 重定向 `/ledger`；导航、余额入口、创作页/广告片页“去充值”改“积分明细”；积分不足文案改“请联系管理员”。

## 3. 怎么合并

```bash
cd /data/workspace/minimax-ui
git fetch origin && git log --oneline -1 origin/main   # 确认与基线关系

# 方式一（推荐，保留三段提交信息）
./apply.sh --check          # 只检查不落盘
git am am/0001-feat-advideo.patch am/0002-feat-worker.patch am/0003-refactor-web.patch

# 方式二（不建提交，改动留在工作区自行提交）
./apply.sh

# 若远端已前进，用三方合并
git am -3 am/*.patch
```

回滚：`./apply.sh --rollback`；若已 `git am`，直接 `git revert` 三个提交。

## 4. 已做的验证（可复跑，命令见 verify.log）

1. `git am` 在**干净 worktree @ ed7dcab** 上顺序应用：exit 0，三段提交齐全
2. 应用后 17 个文件与线上工作区 `cmp` 全 SAME（`MATCH 17/17`），`RechargePage.tsx` 两边均不存在
3. `python3 -m py_compile` 全部 10 个后端文件：`PY_COMPILE_OK`
4. 密钥扫描：`sk-` / `eyJ` / `password` / `DEEPSEEK_API_KEY` / `QWEN_API_KEY` / `api_key=` / 私钥头 / `server/.env` 命中 **全 0**
5. 补丁内无二进制块、无 `.db`、无 `.bak`、无绝对路径

## 5. 合并后必须做的两件事（补丁不含产物）

1. **前端重建**：`cd web && npm ci && npm run build`（`web/dist/` 被 .gitignore 排除，补丁只有源码）
2. **后端重启**：`systemctl restart`（或按现有方式重启 `uvicorn app.main:app --port 8001`）；
   启动时 `_migrate_advideo()` 会自动给 `tasks` 表补 `ad_auto_confirm` 列，无需手工 SQL

不要提交 `server/.env`、`server/clouds/*.env`（含 KEY，已被 .gitignore 排除）。

## 6. 明确**未**纳入补丁的东西（建议保持不入库）

线上工作区还有 280 个未跟踪项，其中 257 个是 `*.bak*` 备份（约 27 MB），其余为一次性产物：
`server/app.db`、`server/minimax.db`、`data/`、`ops-2026100x/`、`tmp_ci/`、`patch_*.py`、
`server/_q*.py`、`advimage.py`、`server/_vdn_backup_*/`、`_rollback_backup_*/`、`advonline-build/`。
这些是本机调试/备份残留，不应进仓库；若后续要纳管 `data/` 下的运行态 JSON，应单独加 .gitignore 规则。

## 7. 给下一个 agent 的环境备注

- 45 的 22 端口从 SAI 容器直连被拒（服务本身在跑，8001/8090/80 均通），**必须经跳板**：
  ```bash
  K=/data/workspace/sai_id_ed25519
  ssh -i $K root@192.168.10.246 "ssh -i /root/.ssh/sai_id root@192.168.10.45 'cd /data/workspace/minimax-ui && git status'"
  ```
- 补丁原件另存：`gpu05:/data/workspace/mmui-patch-20261018/out/`（含 `am/`）
- 验收用的临时 worktree：`/data/workspace/mmui-verify-20261018`（保留，可进去 `git log` 看三段提交）；
  `git am` 与 `apply.sh` 的测试 worktree 已在验收后删除，重建方式见 `verify.log` 与本文第 3 节

## 8. 部署环境需人工处理的环境变量（**只写变量名，值一律不写、不入库**）

### 8.1 本补丁行为相关（新增，均有安全默认值，不设即按默认跑）
| 变量名 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `ADVIDEO_AUTO_CONFIRM` | bool | `true` | 广告片自动模式总开关。设 `false` 即回到“出图后等人工确认”的老行为（**若不想改变现网行为，务必先设 false**） |
| `ADVIDEO_AUTO_CONFIRM_INDEX` | int | `0` | 自动模式默认选第几张候选图（0 起，越界吸附最后一张） |
| `POISON_GUARD` | 字符串开关 | `1` | 毒丸隔离总开关（`0/false/no/off` 关闭） |
| ~~`POISON_RAM_ADMISSION`~~ | — | — | **2026-10-20 已移除**：内存准入会导致所有任务都派发不出去；改为导演台长任务自动拆分（≤30s/块）+ 拼接 |
| ~~`POISON_RAM_MIN_GB`~~ | — | — | **2026-10-20 已移除**，同上 |
| `DIRECTOR_CHUNK_SECONDS` | float | `30` | 导演台单块生成时长上限（秒）；超过按段边界拆分生成再 ffmpeg 拼接 |
| `POISON_GUARD_FILE` | path | `<data>/data/poison_tasks.json` | 熔断名单落盘路径（可选覆盖） |
| `POISON_GUARD_EVENTS` | path | `<data>/data/poison_events.jsonl` | 事件流落盘路径（可选覆盖） |
| `MINIMAX_DATA_DIR` | path | `/data/workspace/minimax-ui/server/data` | 上面两个默认路径的根目录 |

### 8.2 既有、补丁不新增但新代码路径会读到的（部署机 `.env` 里应已有，**只保留在本机，不提交**）
`JWT_SECRET`、`DATABASE_URL`、`DATA_DIR`、`COMFYUI_URL`、`COMFYUI_WORKERS`、`UPSCALE_ENABLED`、`DIRECTOR_ENABLED`、
`ADVIDEO_ENABLED`、`ADVIDEO_IMAGE_WORKER`、`ADVIDEO_IMAGE_PROMPT_MODE`、`ADVIDEO_LLM_PROVIDER`、
`QWEN_API_KEY`、`QWEN_API_BASE`、`QWEN_LLM_MODEL`、`DEEPSEEK_API_KEY`、`OPS_CONTROL_KEY`、`MINIMAX_API_KEY`
> 变量名取自 `minimax-env/env-info/server.env.masked` 与 `server/app/config.py` 字段（pydantic-settings 无 `env_prefix`，变量名 = 字段名大写）。
> 值请在新部署机的 `server/.env` 手工填，**不要**写进任何 patch/文档/聊天记录。

## 9. 冲突高发文件与处理建议

| 文件 | 补丁内改动量 | 为什么易冲突 | 建议处理 |
|---|---|---|---|
| `server/app/services/worker.py` | +134 / -8（01+02 都动） | 全项目最热文件，几乎每天有人改；冲突点集中在 `_dispatch`/领取任务/失败重投三处 | `git am -3`；冲突块逐个保留两侧：`_acquire_with_pin` 包住原 `pool.acquire`，别丢原有的 director/upscale 分支 |
| `server/app/routers/videos.py` | +17 / -21 | 确认接口体被抽到 `advconfirm.apply_confirm`，原函数被改名/瘦身 | 若上游又改过确认逻辑，优先保留上游新逻辑，只把“公共逻辑抽取”照做一遍，别整块覆盖 |
| `server/app/config.py` | +4 | 新增字段插在 advideo 段尾 | 冲突时双方字段都保留，位置不重要（`extra="ignore"`） |
| `web/src/pages/AdvideoPanel.tsx` | +37 / -7（01 与 03 都动） | 01 加“自动模式”开关，03 改“积分明细”入口 | **顺序必须 01 先、03 后**；冲突时保留自动模式开关 + 积分明细文案两者 |
| `web/src/pages/CreatePage.tsx` | +2 / -2 | 前端迭代最频繁（素材选择器/抽屉等改动多） | 只需保住“去充值”→“积分明细” 的链接与文案替换，其余以本地新代码为准 |
| `server/app/models.py` / `main.py` | +2 / +1 | 新增 `Task.ad_auto_confirm` 列 + 启动补列迁移 | 若上游已加别的启动迁移，按顺序都调用即可，`_migrate_advideo()` 可重复执行（幂等） |

通用：远端已前进时一律 `git am -3 am/*.patch`（或 `git apply -3`），**不要** `git checkout -- .` 硬覆盖；
应用前 `git stash` 现场改动、先建分支（例：`git switch -c merge/mmui-local-20261018`），失败可整支丢弃。

## 10. 独立复验（第二套证据，详见 `verify.log` 第 9 节）

在 SAI 容器（无 45 的 worktree、无任何本机缓存）里，用**公网基线快照**（codeload @ `ed7dcab`，515,938 B）
重建基线提交后重新应用本包补丁，再与 45 真机工作区**逐文件 sha1 双向对照**：`MATCH 17 / 17`。
同时核对了完整性（45 上 tracked 改动 15 M + 1 D + 2 新增未跟踪源码 = 17 文件，与本包清单完全一致，无遗漏无夹带），
以及 `apply.sh` 的 apply / --check / --rollback 三条路径（回滚后 `git status` 变更数 = 0）。

> `apply.sh` 的一处提示错误已修正：基线不符时提示的 `am/` 路径原先用 `$PWD`（指向被测仓库），
> 现改为脚本自身目录 `$DIR`，照提示敲命令即可命中。

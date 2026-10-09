# minimax-ui 本地修改 → 远端仓库 差异包（patch）说明

- 生成时间：2026-10-09（CST）｜生成位置：`45:/data/workspace/minimax-ui`
- 对比对象：`origin/main`（`https://github.com/Club6663629/minimax-ui.git`；本地 HEAD 基线 `180770d 海白菜品牌浅色主题 + 创作页分镜模式/资产管理…`）
- 规模：**47 个文件，+6305 / −158 行**；patch 文件 **378,590 B**，md5 `8d6b7d544f31eec528a0183ee470f358`

## 下载地址
| 位置 | 链接 |
|---|---|
| 公网（推荐） | http://minimax.ai4ss.com/advreport/minimax-ui-local-changes.patch |
| 公网（旧约定路径） | http://minimax.ai4ss.com/video/minimax-ui-local-changes.patch |
| 宿主机 45 | `/data/ComfyUI/output/pub/advreport-20261009/minimax-ui-local-changes.patch`；`/data/minimax-ui/output/minimax-ui-local-changes.patch` |
| 本机容器 | `/data/workspace/advreport-20261009/minimax-ui-local-changes.patch` |

## 生成方式（可复现）
```bash
ssh -i /data/workspace/sai_id_ed25519 root@192.168.10.45
cd /data/workspace/minimax-ui
git fetch origin                                   # 已是最新
# 新增文件必须先进 diff（否则 untracked 会被漏掉）
git add -N server/app/legal.py server/app/routers/legal.py server/app/routers/ops.py \
  server/app/services/advenhance.py server/app/services/advimage.py \
  server/app/services/advimage_p2.py server/app/services/advimage_p2b.py \
  server/app/services/advpostir.py server/app/services/advprompt.py \
  server/workflows/advideo_image_api.json \
  web/public/legal/*.html web/src/components/LegalNotice.tsx web/src/legal.ts \
  web/src/lib/humanize.ts web/src/pages/AdvideoPanel.tsx
git diff origin/main -- . ':(exclude)web/dist' ':(exclude)*.bak*' > minimax-ui-local-changes.patch
git reset -q                                       # 还原索引，不留下 -N 痕迹（工作区不变）
```
**索引卫生**：`git status --porcelain | wc -l` 生成前 281 行 → 生成后 281 行（无残留）。

## 验证（不是"生成完就算"）
```bash
git worktree add --detach /tmp/mui-verify origin/main
cd /tmp/mui-verify && git apply --check <patch>   # → APPLY-CHECK: OK
git apply <patch>                                  # → APPLY: OK
git status --short | wc -l                         # → 43（28 改 + 15 新增目录/文件项）
```
即：**该 patch 能干净打到 origin/main 上**。验证后 `git worktree remove --force` 已清理。

## 清单（按目录/功能分类）
> 完整 `git diff --stat` 见本篇末尾。

### A. 电商广告片（advideo）
| 文件 | 说明 |
|---|---|
| `server/app/services/advenhance.py`（新增 581 行） | 提示词增强层：品类识别 + 中性保真锁 + 集合锁 + 视频本地规则（A 臂）；含 60017 品类护栏修复 |
| `server/app/services/advimage.py`（新增 430 行） | 广告图阶段（模板注入 / PE / 落盘 / 审计文件） |
| `server/app/services/advprompt.py`（新增 280 行） | 视频提示词包裹 `[FIDELITY]+[SET]+[CAMERA]+[AUDIO]` |
| `server/app/services/advpostir.py`（新增 166 行） | Content-IR 单镜化后处理 |
| `server/app/services/advimage_p2.py` / `advimage_p2b.py`（新增，371/394 行） | **实验模块，当前无引用**（Phase2 变体），保留供参考，可删 |
| `server/workflows/advideo_image_api.json`（新增 253 行） | 广告图 ComfyUI 模板（qwen-image-2.1 + 保真 PE） |
| `web/src/pages/AdvideoPanel.tsx`（新增 695 行） | 广告片面板 |
| `server/app/routers/videos.py`（+202/−） | advideo 4 端点（status/create/regenerate/confirm-image）+ **管理员闸门** + `/advideo/status` 鉴权 |
| `server/app/routers/files.py`（+24） | `GET /files/adimage/{task_id}` 候选图下载 |
| `server/app/auth.py`（+16） | `get_advideo_access()` 邮箱白名单闸门 |
| `server/app/models.py` / `schemas.py` / `config.py` / `worker.py` | advideo 字段、入参契约、开关、图像/视频阶段循环 |
| `web/src/pages/CreatePage.tsx`（+861/−） | 广告片入口（探针渲染）+ 文案口径 + 主流程 |

### B. 用户协议 / 法务
`server/app/legal.py`、`server/app/routers/legal.py`、`web/src/legal.ts`、`web/src/components/LegalNotice.tsx`、`web/public/legal/{user-agreement,privacy-policy,api-terms,credits-rules,ai-labeling}.html`（新增，共 5 个静态页）+ `routers/auth.py`（记录同意）。

### C. 资产管理 / 上传
`server/app/routers/uploads.py`（+216）：素材列表、删除、去重、音频素材、上传入口；`web/src/pages/AssetPanel.tsx`（+310）。

### D. 运维后台 / 节点池
`server/app/routers/ops.py`（新增 50）、`server/app/services/pool.py`（+104，优先级 worker / A100 间隙跑 / qwen21 置忙）、`web/src/pages/AdminPage.tsx`、`server/app/main.py`（+51：启动补列 + 路由注册）。

### E. 生成/超分模板与加速
`server/workflows/{t2v,flf2v,r2v,director}_api.json`（各 2 处：Lightning LoRA 开关/步数）、`server/app/services/comfyui.py`（+45：超分 tiers 步数、fp8 禁 LoRA 分支、SeedVR2 chunking 适配、输出过滤 input 视频）。

### F. 其它前端
`web/src/api/client.ts`、`components/Layout.tsx`、`components/TaskCard.tsx`、`pages/AuthPage.tsx`、`pages/RechargePage.tsx`、`state/auth.tsx`、`types.ts`、`lib/humanize.ts`、`server/app/scenes.py`、`routers/serialize.py`。

## 明确排除（未纳入 patch）
- 备份与历史产物：`*.bak.*`、`server/_vdn_backup_*`、`server/.env.bak.*`
- 运行时数据：`server/app.db`、`server/data/`、`data/app.db`、`*.log`
- 构建产物：`web/dist/`（368 KB，nginx 直接吃 dist；要部署请 `cd web && npm run build`）
- 调试脚本/素材：`bench_*.py`、`test_*.py`、`tmp_ci/`、`ops-2026100*/`（运维一次性脚本与截图）、根目录 `advimage.py`（旧副本，正式实现在 `server/app/services/advimage.py`）、`patch_*.py`、`fix_migrate.py`（一次性打补丁脚本，最终态已在 diff 里）
- `.env`（含环境变量与凭据；环境变更请按需手动同步）

## 应用方式
```bash
git pull                       # 先到 origin/main
git apply /path/to/minimax-ui-local-changes.patch   # 或 git am（本 patch 未含 commit 信息，用 apply）
cd web && npm run build        # 前端必须重构建
# 后端重启 uvicorn app.main:app --port 8001
```

## 附：完整 git diff --stat
```
 server/app/auth.py                      |  16 +
 server/app/config.py                    |  61 ++-
 server/app/legal.py                     |  86 ++++
 server/app/main.py                      |  51 +-
 server/app/models.py                    |  43 +-
 server/app/routers/auth.py              |   7 +-
 server/app/routers/files.py             |  24 +
 server/app/routers/legal.py             |  35 ++
 server/app/routers/ops.py               |  50 ++
 server/app/routers/serialize.py         |  31 ++
 server/app/routers/uploads.py           | 216 +++++++-
 server/app/routers/videos.py            | 202 +++++++-
 server/app/scenes.py                    |  10 +-
 server/app/schemas.py                   |  96 ++++
 server/app/services/advenhance.py       | 581 +++++++++++++++++++++
 server/app/services/advimage.py         | 430 ++++++++++++++++
 server/app/services/advimage_p2.py      | 371 ++++++++++++++
 server/app/services/advimage_p2b.py     | 394 +++++++++++++++
 server/app/services/advpostir.py        | 166 ++++++
 server/app/services/advprompt.py        | 280 +++++++++++
 server/app/services/comfyui.py          |  45 +-
 server/app/services/pool.py             | 104 +++-
 server/app/services/worker.py           | 227 ++++++++-
 server/workflows/advideo_image_api.json | 253 ++++++++++
 server/workflows/director_api.json      |   2 +-
 server/workflows/flf2v_api.json         |   2 +-
 server/workflows/r2v_api.json           |   2 +-
 server/workflows/t2v_api.json           |   2 +-
 web/public/legal/ai-labeling.html       |  64 +++
 web/public/legal/api-terms.html         |  68 +++
 web/public/legal/credits-rules.html     |  77 +++
 web/public/legal/privacy-policy.html    |  86 ++++
 web/public/legal/user-agreement.html    | 112 +++++
 web/src/api/client.ts                   |  55 +-
 web/src/components/Layout.tsx           |  26 +-
 web/src/components/LegalNotice.tsx      |  60 +++
 web/src/components/TaskCard.tsx         |  17 +-
 web/src/legal.ts                        |  33 ++
 web/src/lib/humanize.ts                 |  13 +
 web/src/pages/AdminPage.tsx             |  18 +-
 web/src/pages/AdvideoPanel.tsx          | 695 ++++++++++++++++++++++++++
 web/src/pages/AssetPanel.tsx            | 310 ++++++++++--
 web/src/pages/AuthPage.tsx              |  70 ++-
 web/src/pages/CreatePage.tsx            | 861 +++++++++++++++++++++++++++++---
 web/src/pages/RechargePage.tsx          |  11 +
 web/src/state/auth.tsx                  |  16 +-
 web/src/types.ts                        |  84 +++-
 47 files changed, 6305 insertions(+), 158 deletions(-)

```

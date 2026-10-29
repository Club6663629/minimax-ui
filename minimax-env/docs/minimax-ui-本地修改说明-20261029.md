# minimax-ui 本地改动 → 可合并补丁包（2026-10-29）

把 HiBTV 短剧工作台的一批缺陷修复整理成 4 个可独立审查的补丁，基线为 `c6ad261`，
可直接 `git am` 顺序合并。

- 基线：`c6ad26160aa4b37ffcdb7bab2343ab402ce292d9`（合并时本地 HEAD 与 `origin/main` 一致）
- 结果树：`acc699429eae5de2c3c9f21bc37b6168bf6a07a8`（与补丁包 `VERIFICATION.log` 记录一致）
- 归档全量 diff：`patches/minimax-ui-local-changes-20261029.patch`（MD5 `c0f02af87a79e5403d2062d2e5d945c8`，840 行）
- 规模：11 个文件，`+512 / -40`

## 1. 四个补丁

| 本地提交 | 补丁来源 | 主题 | 文件 |
|---|---|---|---|
| `a5f11e3` | `74670e6` | 多角色关键帧身份锁、人数硬约束、站位镜号角色绑定、美术页 anchors 兼容 | 7 个（含 1 个新测试） |
| `6bacf4d` | `aadf3b5` | staging 改为按集读写（`EP_KINDS` 补 `staging`） | 2 个 |
| `1e6938f` | `008da07` | 大纲取消态兼容、上游引用本地路径回退、网关 `user` 透传 | 2 个 |
| `3173d6f` | `d798ab6` | 新建项目弹窗挂 layer/scrim、去掉点击项目时冗余 `refreshProject` | 1 个 |

## 2. 各补丁内容

### 01 — 关键帧身份锁与人数硬约束（`a5f11e3`）

| 文件 | 变更 |
|---|---|
| `server/app/services/hibtv_frames.py` | `MAX_REFS` 3 → 4（场景 1 + 全部出场角色，短剧单场最多 3 人）；`prompt_for()` 新增 `characters`/`refs` 参数，逐槽声明 `Picture N = 角色 code/姓名/性别/身份`，写入 `maxOnScreen` 人数硬约束并禁止克隆/分身/相同的脸/多视图拼贴；新增 `character_specs_for()`、`ref_specs_for()`、`ensure_scene_main()`（关键帧开工前缺场景主图则先用同一图片模型生成）；`draw()` 校验「每个具名角色都有可用参考图」，缺失或超上限直接失败而非静默省略；负向词补 `duplicate person/cloned face/character sheet/multi-view grid` 等 |
| `server/app/services/hibtv_image.py` | 新增 `_merge_negative()`：公共与关键帧负向词去重合并，上限 900 → 1600，避免截掉后加入的复制约束 |
| `server/app/services/hibtv_stages.py` | 新增 `_bind_h3_prompt()` / `_script_line_speakers()` / `_dialogue_key()`：把孤立 `S#` 展开为角色 code/姓名绑定块（`[[hibtv_identity_speaker_lock_v1]]`），按 `[Shot N]` 写入人数、出场顺序、口型与台词归属锁；台词无法绑定或说话人不在 `cut.characters` 中时抛 `StageError`；`promptSource` 追加 `+identity-binding`；`_PICTURE_RE` 兼容列表符号前缀；`sync_frames()` 把 `maxOnScreen` 写入帧 meta |
| `web/src/hibtv/pages/ArtPage.tsx` | anchors 兼容字符串与 `{name, desc}` 对象两种形态，修美术页白屏（React #310） |
| `web/src/hibtv/types.ts` | 新增 `ArtAnchor = string \| {name?, desc?}`；`ArtScene.anchors` 改 `ArtAnchor[]`；`ArtProp` 补 `anchors` |
| `server/tests/test_hibtv_gw_materials.py` | **新增（61 行）**：网关素材中转 3 个用例（见补丁 03） |

### 02 — staging 按集读写（`6bacf4d`）

| 文件 | 变更 |
|---|---|
| `server/app/services/hibtv_jobs.py` | `EP_KINDS` = `("script", "staging", "storyboard")` |
| `server/app/routers/hibtv.py` | 同步 `EP_KINDS` 注释；修站位页按集读取恒为 0 场 |

### 03 — 大纲取消态兼容与上游引用回退（`1e6938f`）

| 文件 | 变更 |
|---|---|
| `server/app/services/hibtv_codex.py` | `latest_done()`：作业 `cancelled` 但对应文档 `done` 且有产物时也算上游可用（扫描上限 10 → 50），修项目被永久锁死在「需要上游 outline，但还没有成功产物」；新增 `_resolve_upstream_ref()`：网关绝对路径不可达时回退 `job_dir/<文件名>` → `project_dir/refs/<up>-<文件名>`，修 400 `input materials missing`；新增网关素材中转：`GW_MATERIALS_HOST`/`GW_MATERIALS_MOUNT` 环境变量（带默认值），`write_source()` 与大纲任务 `task_for()` 改为发布并引用容器内路径，复制失败/大小校验不一致直接阻断任务；历史项目仅有 `novel_path` 时自动补建本地 `source.txt`；`submit` 透传 `user=hibtv-u<user_id>` |
| `server/app/services/codex_gateway.py` | `submit()` 新增 `user` 参数并写入请求体 |

> 部署注意：`HIBTV_GW_MATERIALS_HOST` 默认
> `/root/sai-v2/dw-engine/tenants/xiaojun/data/workspace/hibtv-materials`，
> `HIBTV_GW_MATERIALS_MOUNT` 默认 `/data/workspace/hibtv-materials`。
> 若部署机的网关素材目录不同，必须在 `server/.env` 显式覆盖，否则大纲/文本任务会报「网关素材中转失败」。

### 04 — 新建项目弹窗层级（`3173d6f`）

| 文件 | 变更 |
|---|---|
| `web/src/hibtv/pages/HomePage.tsx` | 新建项目 `Modal` 挂到 `layer open` + `scrim`（点遮罩关闭），确保弹窗在屏内；删除点击项目时的冗余 `refreshProject()` |

## 3. 合并方式

本地已用 `git am` 顺序应用 4 个补丁并保留原提交信息，无需重复操作。其他环境：

```bash
cd /data/workspace/minimax-ui
git fetch origin && git log --oneline -1 origin/main   # 应为 c6ad261
git apply --check ../minimax-env/patches/minimax-ui-local-changes-20261029.patch
git apply         ../minimax-env/patches/minimax-ui-local-changes-20261029.patch
```

回滚：`git revert 3173d6f 1e6938f 6bacf4d a5f11e3`（或按主题单独 revert）。

## 4. 已验证

1. `git am` 四个补丁顺序应用：exit 0，无冲突，结果树 `acc6994` 与补丁包声明一致
2. 后端单测：`venv/bin/python -m unittest tests.test_hibtv_gw_materials -v` → 3 passed
3. `python3 -m compileall -q server/app` → exit 0
4. 前端 `npm run build`（tsc + vite）→ exit 0
5. `git diff --check c6ad261..HEAD` → exit 0
6. 密钥扫描：补丁中无 key-like 内容，无 `.env`、备份、调试或 reject 文件

## 5. 行为变化与风险

- 关键帧参考图上限由 3 提到 4：单场具名角色 > 3（含场景即参考图 > 4）时会**直接失败**并提示补主图或拆镜，不再静默省略角色。
- 关键帧缺场景主图时会**自动先出场景主图**（消耗一次图片任务）。
- 分镜导入时若 `[Shot N]` 中台词无法绑定角色，或说话人不在 `cut.characters`，导入会抛 `StageError`（宁可失败不串脸）。
- 大纲/文本任务改走网关素材目录（`source.txt` 中转），需确认部署机上述两个环境变量路径可写。

# minimax-ui 本地修改说明（相对 origin/main）

- 仓库：`https://github.com/Club6663629/minimax-ui.git`，分支 `main`
- 基线：`origin/main` = 本地 HEAD = `ad3dd2b 更新 skills，充分利用 content-IR能力`（本地未领先未落后）
- patch：`patches/minimax-ui-local-changes.patch`（15 文件，**+321 / −265**）
- 生成方式：`cd /data/workspace/minimax-ui && git fetch origin && git diff origin/main`（新增文件 `upscale_rtx_api.json` 以 intent-to-add 纳入）

## 修改总览
| 文件 | 增删 | 说明 |
|---|---|---|
| `server/app/config.py` | +3 | 新增 `comfyui_input_dir`（VOSR2 L2 缓存源路径用）、`cost_4k_extra=30` |
| `server/app/routers/files.py` | ±4 | 下载支持 `4k` 分辨率 |
| `server/app/routers/serialize.py` | ±6 | 序列化新增 4K 成片 URL，`video_url` 优先指向 4k |
| `server/app/routers/videos.py` | ±3 | 定价返回 `cost_4k_extra`；4K 依赖本地超分池校验 |
| `server/app/schemas.py` | ±5 | `resolution` 字面量增加 `"4k"`，`UpgradeIn`/`PricingOut` 同步 |
| `server/app/services/billing.py` | +4 | 计费/升级支持 4K 档 |
| `server/app/services/comfyui.py` | +44 | 4K 档位、VOSR2/RTX 注入分支、`src_abs_path` 参数 |
| `server/app/services/worker.py` | +14/-… | 引擎→模板映射 `rtx→upscale_rtx`；传源片绝对路径；4K 后缀落盘 |
| `server/workflows/flf2v_api.json` `r2v_api.json` `t2v_api.json` | 大幅 | 采样器改 `euler`，固化 8step turbo LoRA（去运行时 If/Else 切换） |
| `server/workflows/upscale_rtx_api.json` | +116（新增） | VOSR2 1× 缓存 + RTX VSR 超分模板 |
| `web/src/api/client.ts` `CreatePage.tsx` `types.ts` | ±18 | 前端分辨率选项加 4K、显示 4K 升级价 |

## 关键改动细节

### 1. 新增 4K 档位（全链路）
- `comfyui.py` `UPSCALE_TIERS` 增加 `4k`：`short_side=2160`（3840×2160，~2.8×）；
  `UPSCALE_7B_RESOLUTION` 增加 `"4k"`（16:9→2160，9:16→3840，1:1→2160）。
- `billing.py` / `videos.py` / `schemas.py` / `files.py` / `serialize.py` / `worker.py` 同步支持 `4k` 计费、校验、落盘后缀 `_4k.mp4` 与下载。
- 前端 `CreatePage.tsx` 分辨率列表由 `["768p","1k","2k"]` 改为 `["768p","2k","4k"]`，升级按钮改为 2K/4K，价格取 `cost_2k_extra` / `cost_4k_extra`。

### 2. 生成模板（flf2v / r2v / t2v）
- 采样器 `res_multistep` → `euler`。
- 移除运行时的「If/Else Switch（Model）+（Steps）」条件分支，改为固定链路：
  `模型 → MiniMaxH3SigmaShift(shift_video=6.0, shift_audio=3.0) → LoraLoaderModelOnly(turbo_8step_v1.0_768p) → BasicScheduler(steps=8, simple)`。
- 即：**默认启用 turbo LoRA / 8 步**，模板不再按节点运行时切换。

### 3. 超分模板 `upscale_rtx_api.json`（新增，VOSR2 + RTX VSR）
- `VOSR2ModelLoader(model="VOSR2", dtype="fp16")` → `VOSR2UpscaleCached`（L2 磁盘缓存）→ RTX VSR。
- 缓存键相关参数在 `comfyui.py` 中**固定为跨档位一致**：`upscale=1`、`seed=42`（固定常量，禁止 random）、`color_alignment="wavelet"`、`tile_size=1024`、`tile_overlap=32`、`vae_tile_size=1024`、`vae_tile_overlap=32`、`source_extra='{"src...`（固定常量，禁止塞入 tier/分辨率，否则 2K/4K 永不共享缓存）。
- `source_path` 由 `worker.py` 传入源片在超分节点本地的绝对路径：
  `comfyui_input_dir`（默认 `/data/ComfyUI/input`）+ 文件名。

### 4. worker 引擎映射
```python
_engine_tpl = {"seedvr2": "upscale_7b", "seedvr2_3090": "upscale_3090", "rtx": "upscale_rtx"}
template = _engine_tpl.get(node.engine, "upscale")
```
- 节点 `engine=rtx` → `upscale_rtx` 模板；`seedvr2` → 原生 7B 模板；默认 KSampler 管线。

## 未纳入 patch
仓库中 44 个 untracked 文件多为调试产物（`*.bak.*`、`.env.bak.*`、`uvicorn_8001.log.bak.*` 等），**非交付内容，已排除**；仅 `server/workflows/upscale_rtx_api.json` 为正式新增文件，已纳入。

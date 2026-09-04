# MiniMax H3 Studio

基于 **MiniMax H3 + ComfyUI** 的 AI 视频创作网站，参考海螺 AI（hailuoai.com）的产品形态，包含：

- 用户注册 / 登录（JWT）
- 视频创作：统一输入卡（参考图上传 + 提示词），是否上传参考图与「全能参考 / 首尾帧」选项共同决定生成模式（文生视频 / 首尾帧 / 全能参考），画幅 / 时长 / 分辨率（768p / 1K / 2K）配置
- 任务队列与状态流转、视频预览与下载、失败重试（积分自动退还）
- **多卡并发**：生成与超分双队列调度，跨机 ComfyUI Worker 池（心跳探活、故障自动摘除/恢复）
- 积分计费：注册赠送、消耗 / 退还 / 充值流水（使用台账）
- 兑换码充值 + 轻量管理后台（用户管理、兑换码生成、任务监控、用量统计、**Worker 池监控**）

编排器（本服务）内置调度器，统一指挥多台 ComfyUI 实例；无新中间件，SQLite 即队列：

```
浏览器 ──▶ Nginx(:80) ─┬─ /           → web/dist（前端静态资源）
                        └─ /api,/files → FastAPI 编排器(:8000)
                                             │
                                             ├─ SQLite（用户/任务/积分/兑换码 + 队列状态）
                                             ├─ 双队列调度器：生成队列 ⇄ 超分队列（进程内）
                                             ├─ ComfyUI Worker 池（内网多机，心跳 10s）
                                             │    ├─ generate 节点 × N（H3 768p 生成）
                                             │    └─ upscale  节点 × M（SeedVR2 1K/2K 超分）
                                             └─ 云端 API（可选）：提示词增强 / 队列降级 / 2K 回落
```

任务状态机：`queued → enhancing(可选) → generating_768p → upscaling(1k/2k，可选) → done / failed`

- 领取生成任务时扣积分，失败 / 重试耗尽全额退还；重投不重复计费
- 每个 ComfyUI 实例并发=1（串行执行），多卡并发由池内多实例提供
- 生成槽出片即释放，1K/2K 任务进入独立超分队列，生成与超分流水线并行

## 目录结构

```
├── server/                 # FastAPI 后端（编排器）
│   ├── app/
│   │   ├── config.py       # 配置（环境变量/.env，含集群池配置）
│   │   ├── models.py       # users / tasks / credit_logs / redeem_codes / uploads
│   │   ├── auth.py         # JWT + bcrypt
│   │   ├── routers/        # auth / videos / uploads / files / credits / admin
│   │   └── services/
│   │       ├── billing.py  # 计费与积分流水（768p/1K/2K 三档）
│   │       ├── pool.py     # ComfyUI Worker 池：注册/心跳自愈/按角色档位路由
│   │       ├── worker.py   # 双队列调度器（增强/生成/超分三个常驻循环）
│   │       ├── comfyui.py  # ComfyUI 客户端 + 工作流注入（生成 + 双引擎超分）
│   │       └── cloud.py    # 云端客户端：提示词增强 / 全流程生成 / 2K 重生成
│   ├── workflows/          # ComfyUI API 格式工作流模板（官方模板转换，见 _official/convert.py）
│   └── create_admin.py     # 创建管理员
├── web/                    # React + Vite + Tailwind 前端（暗色主题）
└── deploy/                 # nginx / systemd / .env 模板
```

## 一、本地开发

### 后端（Python 3.9+）

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 无 ComfyUI 的机器用 Mock 模式联调：模拟 4 生成 + 3 超分虚拟节点，
# 任务全流程流转，用 ffmpeg 生成测试视频
MOCK_COMFY=1 JWT_SECRET=dev-secret uvicorn app.main:app --reload --port 8000
```

创建管理员（二选一）：

```bash
python create_admin.py admin@example.com admin admin123
# 或启动时通过环境变量：ADMIN_EMAIL / ADMIN_PASSWORD
```

### 前端（Node 18+）

```bash
cd web
npm install
npm run dev        # http://localhost:5173，/api 与 /files 自动反代到 :8000
```

## 二、生产部署

### 单机部署（编排器与 ComfyUI 同机）

```bash
# 1. 放置代码
sudo mkdir -p /opt/minimax-ui && sudo cp -r . /opt/minimax-ui

# 2. 后端
cd /opt/minimax-ui
python3.11 -m venv venv
./venv/bin/pip install -r server/requirements.txt
cp deploy/.env.example server/.env   # 编辑：JWT_SECRET、MINIMAX_API_KEY、计费参数
sudo mkdir -p /data/minimax-ui && sudo chown comfy /data/minimax-ui
sudo cp deploy/minimax-ui-api.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now minimax-ui-api

# 3. 前端
cd web && npm install && npm run build     # 产物在 web/dist

# 4. Nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/minimax-ui
sudo ln -s /etc/nginx/sites-available/minimax-ui /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

不配置 `COMFYUI_WORKERS` 时，退回 `COMFYUI_URL` 单实例串行模式（行为与旧版一致）。

### 多节点集群部署（多卡并发）

编排器部署在控制面机器（机房 9 卡方案为 gpu05 / 192.168.10.45），详见《H3集群部署方案-机房9卡版.md》。

**1. 各机 ComfyUI：每卡一个 systemd 实例**

`/etc/systemd/system/comfy-worker@.service`（实例名=端口），`/etc/comfy/<端口>.env` 指定
`CUDA_VISIBLE_DEVICES` 与启动参数。需要被编排器跨机访问的实例，`--listen` 改为内网 IP：

```bash
sudo systemctl enable --now comfy-worker@8188 comfy-worker@8189 ...
curl -s http://<机器>:<端口>/system_stats   # 验证可达
```

**2. 编排器声明 Worker 池**（`server/.env` 的 `COMFYUI_WORKERS`）

条目间分号分隔，每项 `url|角色|标签`（标签内逗号分隔）：

- 角色：`generate`（768p 生成）/ `upscale`（SeedVR2 超分）
- 标签：
  - `heavy`：长片段（>10s）任务优先派发
  - `1k` / `2k`：可承接的超分档位（2K 优先派发）
  - `overflow`：仅主力超分节点全忙时承接 1K
  - `unet:<权重文件名>`：超分节点注入的权重（3B FP16 等）
  - `unet:<mode族>:<文件名>`：生成节点注入的权重（t2v/flf2v → `fl2va`，r2v → `ref2va`）
  - `engine:seedvr2`：用原生节点模板；缺省（`seedvr2_int8`）用 KSampler 管线模板（模板默认 3B FP16 权重）

9 卡机房示例（生成 4 + 超分主力 3 + 1K 溢出 2）：

```bash
COMFYUI_WORKERS="\
http://192.168.10.45:8188|generate|heavy;\
http://192.168.10.51:8188|generate|;\
http://192.168.10.246:8188|generate|;\
http://192.168.10.246:8189|generate|;\
http://192.168.10.45:8189|upscale|1k,2k,engine:seedvr2,unet:seedvr2_3b_fp16.safetensors;\
http://192.168.10.45:8190|upscale|1k,2k,engine:seedvr2,unet:seedvr2_3b_fp16.safetensors;\
http://192.168.5.205:8188|upscale|1k,2k,engine:seedvr2,unet:seedvr2_3b_fp16.safetensors;\
http://192.168.10.45:8191|upscale|1k,overflow,unet:seedvr2_3b_fp16.safetensors;\
http://192.168.10.246:8190|upscale|1k,overflow,unet:seedvr2_3b_fp16.safetensors"
```

**3. 调度与自愈行为**

- 心跳：每 10s 探活全部节点 `/system_stats` 与 `/queue`（ComfyUI 真实队列有 running/pending 即视为忙），连续失败 30s 摘除，恢复后自动回池
- 路由：空闲生成节点 round-robin 轮询派发（长片段优先 heavy 节点）；超分 2K 优先、主力池优先，`overflow` 节点兜底 1K
- 孤儿恢复：后端重启后自动扫描中间态任务（含派发后未提交的窗口期任务），按节点 `/history` 恢复产物或回退重投
- 失败自愈：阶段失败且 `attempts < 2` 自动回退重投；2K 重试耗尽且配置了云端 Key → 回落云端重生成
- 云端降级：生成队列深度超过 `DEGRADE_QUEUE_DEPTH` 且配置了云端 Key → 最老排队任务转云端全流程
- 监控：管理后台「Worker 池」页实时显示各节点健康/忙闲/当前任务与队列深度

**4. 权重分发**：各机本地留存所需权重（生成节点 = H3 INT8/fp8；超分节点统一 = SeedVR2
`seedvr2_3b_fp16.safetensors` + `seedvr2_ema_vae_fp16.safetensors`），
从母本机 `rsync` 分发后软链接进各实例 `ComfyUI/models/` 对应子目录。

## 三、配置项（环境变量 / server/.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `JWT_SECRET` | - | **必改**，登录令牌签名密钥 |
| `DATA_DIR` / `DATABASE_URL` | `./data` | 数据目录与 SQLite 路径 |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI 地址（未配置 `COMFYUI_WORKERS` 时使用） |
| `COMFYUI_WORKERS` | 空 | Worker 池声明，格式见上文「多节点集群部署」 |
| `UPSCALE_ENABLED` | `true` | 本地超分池开关（关闭后 1K/2K 档不可提交） |
| `DEGRADE_QUEUE_DEPTH` | `8` | 生成队列深度超阈值且有云端 Key → 云端全流程降级 |
| `CLOUD_UPSCALE_FALLBACK` | `true` | 本地超分重试耗尽后，2K 回落云端重生成 |
| `MOCK_COMFY` | `0` | 联调模拟模式（模拟 4 生成 + 3 超分节点，无需 ComfyUI） |
| `MINIMAX_API_KEY` | 空 | 配置后启用提示词增强 / 云端降级 / 2K 回落；为空则纯本地出片 |
| `MINIMAX_API_BASE` | `https://api.minimax.io` | 云端 API 基址 |
| `SIGNUP_BONUS` | `50` | 注册赠送积分 |
| `COST_768P_5S` / `COST_768P_10S` | `10` / `20` | 768p 生成计费 |
| `COST_1K_EXTRA` / `COST_2K_EXTRA` | `8` / `15` | 1K / 2K 升级附加计费 |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | 空 | 首次启动自动创建管理员 |

## 四、联调注意事项

1. **工作流模板**：`server/workflows/` 下模板均由 ComfyUI 官方模板
   （Comfy-Org/workflow_templates）经 `_official/convert.py` 转换（`python3 convert.py` 可复跑）：
   - `{t2v,flf2v,r2v}_api.json`：MiniMax H3 生成（注入对齐官方原生节点：`ResolutionSelector`
     画幅预设 + 0.98 百万像素、`PrimitiveFloat` 时长、`prompt` 注入、`seed` 随机、`LoadImage` 按模式重建）
   - `upscale_api.json`：SeedVR2 视频超分（KSampler 管线，默认 3B FP16 权重；注入放大倍数、分时帧批 21/重叠 3 帧、步数按档位）
   - `upscale_7b_api.json`：SeedVR2 视频超分（原生 `SeedVR2VideoUpscaler` 节点，默认 3B FP16 权重；
     注入 `resolution`——16:9 按输出高度、竖屏取长边——并对齐 `ImageScale`，注入时自动移除模板自带的 96 帧截断限制）
   - `upscale_{7b,3b}_image_api.json`：SeedVR2 图像版（评估对照，未接入生产调度）
2. **云端 API 路径**：`server/app/services/cloud.py` 中提示词增强、全流程生成与 2K 重生成的
   接口路径按平台文档编写，联调时如与当日官方文档有出入，只需修改该文件常量。
3. 充值目前走**兑换码**（管理员在后台生成），未接真实支付；后续接入支付宝/微信时，
   在 `routers/credits.py` 增加支付回调并按同样方式调用 `billing.add_credits` 记账即可。

## 五、安全基线

- 网站经 Nginx 统一入口；ComfyUI 不暴露公网——单机仅监听 127.0.0.1，
  集群内各节点只监听内网网卡，防火墙仅放行编排器地址访问 8188–8199
- 密码 bcrypt 哈希；文件下载需 JWT 鉴权且校验归属
- `JWT_SECRET` 与 `MINIMAX_API_KEY` 只存在于编排器 `.env`，不入库、不入代码、不下发到 worker 机器

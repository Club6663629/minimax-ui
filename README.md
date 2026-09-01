# MiniMax H3 Studio

基于 **MiniMax H3 + ComfyUI** 的 AI 视频创作网站，参考海螺 AI（hailuoai.com）的产品形态，包含：

- 用户注册 / 登录（JWT）
- 视频创作：统一输入卡（参考图上传 + 提示词），是否上传参考图与「全能参考 / 首尾帧」选项共同决定生成模式（文生视频 / 首尾帧 / 全能参考），画幅 / 时长 / 分辨率配置
- 任务队列与状态流转、视频预览与下载、失败重试（积分自动退还）
- 积分计费：注册赠送、消耗 / 退还 / 充值流水（使用台账）
- 兑换码充值 + 轻量管理后台（用户管理、兑换码生成、任务监控、用量统计）

架构上与你已按《MiniMax-H3-ComfyUI-INT8-部署方案.md》部署的 ComfyUI 服务对接，网站与后端同机部署：

```
浏览器 ──▶ Nginx(:80) ─┬─ /           → web/dist（前端静态资源）
                        └─ /api,/files → FastAPI(:8000) ─▶ ComfyUI 127.0.0.1:8188
                                             │
                                             ├─ SQLite（用户/任务/积分/兑换码）
                                             └─ 云端 API（可选）：H3-Context-IR 增强 / 2K 重生成
```

任务状态机：`queued → enhancing(可选) → generating_768p → upscaling_2k(可选) → done / failed`
（进入生成阶段扣积分，失败全额退还；GPU 串行单并发，与部署方案 8.2 一致）

## 目录结构

```
├── server/                 # FastAPI 后端
│   ├── app/
│   │   ├── config.py       # 配置（环境变量/.env）
│   │   ├── models.py       # users / tasks / credit_logs / redeem_codes / uploads
│   │   ├── auth.py         # JWT + bcrypt
│   │   ├── routers/        # auth / videos / uploads / files / credits / admin
│   │   └── services/
│   │       ├── billing.py  # 计费与积分流水
│   │       ├── worker.py   # 任务队列 Worker（单并发状态机）
│   │       ├── comfyui.py  # ComfyUI 客户端 + 工作流注入
│   │       └── cloud.py    # H3-Context-IR / Regenerate-2K 云端客户端
│   ├── workflows/          # ComfyUI API 格式工作流模板（官方模板转换，见 _official/convert.py）
│   └── create_admin.py     # 创建管理员
├── web/                    # React + Vite + Tailwind 前端（暗色主题）
└── deploy/                 # nginx / systemd / .env 模板
```

## 一、本地开发

### 后端（Python 3.11+）

```bash
cd server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 无 ComfyUI 的机器用 Mock 模式联调（模拟任务流转，用 ffmpeg 生成测试视频）
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

## 二、生产部署（与 ComfyUI 同机）

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

访问 `http://<机器>` 即可。ComfyUI 仍按部署方案监听 127.0.0.1:8188，不暴露公网。

## 三、配置项（环境变量 / server/.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `JWT_SECRET` | - | **必改**，登录令牌签名密钥 |
| `DATA_DIR` / `DATABASE_URL` | `./data` | 数据目录与 SQLite 路径 |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI 地址 |
| `MOCK_COMFY` | `0` | 联调模拟模式（无需 ComfyUI） |
| `MINIMAX_API_KEY` | 空 | 配置后启用提示词增强与 2K 升级；为空则纯本地 768p |
| `MINIMAX_API_BASE` | `https://api.minimax.io` | 云端 API 基址 |
| `SIGNUP_BONUS` | `50` | 注册赠送积分 |
| `COST_768P_5S` / `COST_768P_10S` | `10` / `20` | 768p 生成计费 |
| `COST_2K_EXTRA` | `15` | 2K 升级附加计费 |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | 空 | 首次启动自动创建管理员 |

## 四、联调注意事项

1. **工作流模板**：`server/workflows/{t2v,flf2v,r2v}_api.json` 由 ComfyUI 官方模板
   （Comfy-Org/workflow_templates 的 MiniMax H3 T2V / I2V / R2V）转换而来，
   原始模板与转换器在 `server/workflows/_official/`（`python3 convert.py` 可复跑）。
   注入逻辑对齐官方原生节点：`ResolutionSelector` 画幅预设 + 0.98 百万像素（768p 原生画布）、
   `PrimitiveFloat` 时长秒数、`prompt` / `PrimitiveStringMultiline` 提示词、`seed` 随机、
   `LoadImage` 按模式动态重建（首/尾帧可只提供其一；全能参考最多 9 张，提示词用 `<Picture N>` 按序引用）。
2. **云端 API 路径**：`server/app/services/cloud.py` 中 H3-Context-IR 与 2K 重生成的接口路径按
   平台文档编写，联调时如与当日官方文档有出入，只需修改该文件常量。
3. 充值目前走**兑换码**（管理员在后台生成），未接真实支付；后续接入支付宝/微信时，
   在 `routers/credits.py` 增加支付回调并按同样方式调用 `billing.add_credits` 记账即可。

## 五、安全基线

- ComfyUI 不暴露公网（仅 127.0.0.1），网站经 Nginx 统一入口
- 密码 bcrypt 哈希；文件下载需 JWT 鉴权且校验归属
- `JWT_SECRET` 与 `MINIMAX_API_KEY` 只存在于 `.env`，不入库不入代码

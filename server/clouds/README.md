# 云端实例（远程开机/关机）配置

一台云端实例 = 一个 `.env` 文件，文件名建议 `<platform>-<uuid>.env`。

- 格式化模板见 `example.env`
- **真实实例的 `.env` 已被 .gitignore 排除**（含 API token），不要提交、不要打进 patch 包
- 目录缺失或为空 = 无云端节点，前端不渲染任何开关机按钮，行为与单机部署一致
- 平台支持：`autodl`（后续可再加 `ali` / `tencent` / `volcano`，在
  `app/services/clouds/` 下新增一个 Provider 并在 `registry._PROVIDERS` 注册）

字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `CLOUD_ID` | 是 | UI 展示的"平台:实例ID"，如 `autodl:pro-7889ca37d10f` |
| `CLOUD_PLATFORM` | 是 | 平台键，决定用哪个 Provider（当前仅 `autodl`） |
| `CLOUD_INSTANCE_UUID` | 是 | 平台侧实例 ID |
| `CLOUD_WORKER_URL` | 是 | **必须与 COMFYUI_WORKERS 中该节点的 url 严格一致**（唯一映射键） |
| `CLOUD_API_BASE` | 是 | 平台 API 根地址，如 `https://api.autodl.com` |
| `CLOUD_API_TOKEN` | 是 | 平台 API Token（仅后端内存使用，不回传前端、不进日志） |
| `CLOUD_DISPLAY_NAME` | 否 | 展示名，如 `AutoDL-5090(bj-B2)` |
| `CLOUD_BOOT_COMMAND` | 否 | 开机后执行的命令（建议指向云端数据盘上的幂等启动脚本） |
| `CLOUD_READY_TIMEOUT` | 否 | 开机→就绪最长期望时长（秒，默认 600） |
| `CLOUD_READY_POLL` | 否 | 状态轮询间隔（秒，默认 10） |
| `CLOUD_ALLOW_POWER_OFF` | 否 | `false` = 该实例只允许开机，不允许关机（默认 true） |

用法：把文件放到 `server/clouds/`，`chmod 600`，重启后端即生效（或把目录放到持久盘并用
主 `.env` 的 `CLOUDS_DIR` 指过去）。

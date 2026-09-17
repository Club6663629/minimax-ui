# 云端实例（AutoDL）接入说明

配套功能：Worker 池页「平台 / 实例 / 状态 / 开机 · 关机」按钮（手动开关机）。
设计文档：`/data/memory/autodl-开关机-方案V2-20260915.md`（服务器 45 的 `/data/memory/`）。

## 1. 云端一次性准备：开机自启脚本

把 `deploy/autodl/start_comfy.sh` 放到**云端数据盘**（AutoDL 数据盘 = `/root/autodl-fs`，实例重启不丢）：

```bash
# 在云端实例上
cp start_comfy.sh /root/autodl-fs/start_comfy.sh
chmod +x /root/autodl-fs/start_comfy.sh
```

脚本行为：幂等（8188 已在监听则直接退出）→ `nohup ./venv/bin/python main.py --listen 0.0.0.0 --port 8188 --use-sage-attention`（工作目录 `/root/autodl-fs/ComfyUI`，日志追加 `/root/autodl-fs/comfy.log`）→ 最多等 120s 端口就绪。
实测：ComfyUI 从启动到端口就绪约 **94 秒**。

> 幂等判定用「端口是否监听」而不是 `pgrep -f main.py`：远程执行时命令行字符串里含关键字会让 pgrep 误判成"已在运行"（实测踩过）。

## 2. 控制面配置：clouds/*.env（一台实例一个文件，600 权限，不入 git）

```bash
cd server/clouds
cp example.env autodl-<instance_uuid>.env
# 填 CLOUD_API_TOKEN / CLOUD_INSTANCE_UUID / CLOUD_WORKER_URL，然后
chmod 600 autodl-<instance_uuid>.env
```

关键点：
- `CLOUD_WORKER_URL` 必须与 `COMFYUI_WORKERS` 里该节点 URL **完全一致**（绑定键；对不上后端只告警、按钮不可用，防止误操作其它实例）。
- `CLOUD_BOOT_COMMAND=bash /root/autodl-fs/start_comfy.sh`：随 `power_on` 一起下发，开机后自动拉起 ComfyUI。
- 主 `.env` 不需要任何改动；`server/clouds/` 不存在时 = 单机部署（前端不渲染开关机按钮）。

## 3. 反向隧道（固定 IP 的云端实例）

控制面 gpu05 上 `comfyui-cloud-tunnel.service`（systemd，`Restart=always`）把云端 8188 映射到本地 8183：

```
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -L 0.0.0.0:8183:127.0.0.1:8188 -p 38561 root@connect.bjb2.seetacloud.com
```

实例开机后隧道自动重连，无需重启后端。

## 4. 前端

Worker 池页面：非云节点 `platform=null` → 不渲染任何按钮；云节点显示 `autodl:pro-xxxxxxxxxxxx` / 实例状态，并提供「开机」「关机」（点击即执行，带二次确认）。

## 5. 后端接口

- `GET  /api/admin/clouds`：实例列表（状态 / 规格白名单字段；**绝不返回 token**）
- `POST /api/admin/clouds/power` `{"url": "<worker url>", "action": "on"|"off"}`：手动开/关机；实例 uuid 由后端按配置自查；平台失败原样透传 `msg` + `request_id`

#!/bin/bash
# 开机自动拉起 ComfyUI（由 AutoDL start_command 调用；幂等，可重复执行）
# 节点：RTX 5090 / 端口 8188 / SageAttention；日志 /root/autodl-fs/comfy.log
# 创建：2026-09-15（SAI）；依赖 /root/autodl-fs/ComfyUI
# 幂等判定用"端口是否已监听"（不用 pgrep -f：远程命令字符串里含关键字会误判）
LOG=/root/autodl-fs/comfy.log
COMFY_DIR=/root/autodl-fs/ComfyUI
port_up() { (exec 3<>/dev/tcp/127.0.0.1/8188) 2>/dev/null; }
cd "$COMFY_DIR" || { echo "$(date '+%F %T') start_comfy.sh: 目录不存在 $COMFY_DIR" >> "$LOG"; exit 1; }
if port_up; then
  echo "$(date '+%F %T') start_comfy.sh: 8188 已在监听，ComfyUI 无需重启" >> "$LOG"
  exit 0
fi
echo "$(date '+%F %T') start_comfy.sh: 启动 ComfyUI" >> "$LOG"
setsid nohup ./venv/bin/python main.py --listen 0.0.0.0 --port 8188 --use-sage-attention >> "$LOG" 2>&1 < /dev/null &
for i in $(seq 1 60); do
  if port_up; then
    echo "$(date '+%F %T') start_comfy.sh: 端口 8188 就绪（约 $((i*2)) 秒）" >> "$LOG"
    exit 0
  fi
  sleep 2
done
echo "$(date '+%F %T') start_comfy.sh: 120 秒内端口未就绪" >> "$LOG"
exit 1

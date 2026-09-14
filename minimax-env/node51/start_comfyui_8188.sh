#!/bin/bash
# 51 生成节点 ComfyUI 启动脚本（sage attention）
kill $(pgrep -f 'main.py --listen 0.0.0.0 --port 8188') 2>/dev/null
sleep 2
cd /data/ComfyUI && nohup ./venv/bin/python main.py --listen 0.0.0.0 --port 8188 --use-sage-attention --vram-headroom 2 > /tmp/comfyui.log 2>&1 &
echo started pid=$!

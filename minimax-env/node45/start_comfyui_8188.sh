#!/bin/bash
# 45 生成节点 ComfyUI 启动脚本（A100，sage attention + headroom 8 + fp16-vae）
kill $(pgrep -f "main.py --listen 0.0.0.0 --port 8188") 2>/dev/null
sleep 2
cd /data/ComfyUI && nohup ./venv/bin/python main.py --listen 0.0.0.0 --port 8188 --use-sage-attention --vram-headroom 8 --fp16-vae > /tmp/comfyui_8188.log 2>&1 &
echo started pid=$!

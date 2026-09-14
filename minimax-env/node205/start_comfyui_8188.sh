#!/bin/bash
# 205 超分节点 ComfyUI 启动脚本（3090，RTX Video Super Resolution 方案，NVIDIA VFX）
kill $(pgrep -f 'main.py --listen 0.0.0.0 --port 8188') 2>/dev/null
sleep 2
cd /data/ComfyUI && CUDA_VISIBLE_DEVICES=0 nohup ./venv/bin/python main.py --listen 0.0.0.0 --port 8188 --fp16-vae > /tmp/comfyui_8188.log 2>&1 &
echo started pid=$!

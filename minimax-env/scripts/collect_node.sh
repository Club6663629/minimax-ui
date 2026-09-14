#!/bin/bash
# 只读采集：ComfyUI 节点环境
C=${COMFY_DIR:-/data/ComfyUI}
echo "##### HOST #####"
hostname; hostname -I 2>/dev/null; date -u +%Y-%m-%dT%H:%M:%SZ
echo "##### OS/KERNEL #####"
grep -E '^(PRETTY_NAME|VERSION)=' /etc/os-release 2>/dev/null; uname -r
echo "##### GPU #####"
nvidia-smi --query-gpu=index,name,memory.total,driver_version,compute_cap --format=csv 2>&1
echo "##### COMFY DIR #####"
ls -ld $C 2>&1
echo "##### COMFYUI VERSION (git) #####"
git -C $C log -1 --format='%H|%ci|%s' 2>&1
git -C $C describe --tags 2>&1 | head -1
cat $C/comfyui_version.py 2>/dev/null
echo "##### PYTHON / TORCH #####"
if [ -x $C/venv/bin/python ]; then PY=$C/venv/bin/python; else PY=python3; fi
$PY -c "import sys,torch;print('python',sys.version.split()[0]);print('torch',torch.__version__);print('cuda',torch.version.cuda);print('cudnn',torch.backends.cudnn.version());print('gpu',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NA');print('cap',torch.cuda.get_device_capability(0) if torch.cuda.is_available() else 'NA')" 2>&1
echo "##### PORTS #####"
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ':(8188|8189|8190|8183)\b'
echo "##### COMFY PROCESSES #####"
for pid in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
  cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)
  case "$cmd" in *main.py*) echo "PID $pid: $cmd";; esac
done
echo "##### START SCRIPTS #####"
for f in /data/start_comfyui_8188.sh /data/start_comfyui_8189.sh /data/start_comfyui_8190.sh /root/start_comfyui*.sh; do
  [ -f "$f" ] && { echo "--- $f ---"; cat "$f"; }
done
echo "##### extra_model_paths.yaml #####"
for f in $C/extra_model_paths.yaml $C/extra_model_paths.yaml.example; do [ -f "$f" ] && { echo "--- $f ---"; cat "$f"; }; done
echo "##### MODELS TREE #####"
for d in diffusion_models text_encoders vae loras upscale_models model_patches clip_vision; do
  echo "== $d =="
  ls -la $C/models/$d 2>/dev/null | grep -v '^total'
done
echo "##### MODEL REAL SIZES (top) #####"
find $C/models -maxdepth 2 -type f \( -name '*.safetensors' -o -name '*.sft' -o -name '*.ckpt' -o -name '*.pth' \) -exec ls -lLh {} \; 2>/dev/null | awk '{print $5, $9}'
echo "##### CUSTOM NODES #####"
ls -la $C/custom_nodes 2>/dev/null
for d in $C/custom_nodes/*/; do
  [ -d "$d/.git" ] && echo "$(basename $d): $(git -C $d log -1 --format='%h %ci %s' 2>/dev/null)"
done
echo "##### VERSION FILES #####"
for f in $C/requirements.txt $C/requirements/*.txt; do [ -f "$f" ] && echo "(exists) $f"; done
echo "##### KEY CONFIG (video_types / nvenc) #####"
grep -rn "h264_nvenc\|hevc_nvenc\|libx264" $C/comfy_api 2>/dev/null | head -20
echo "##### CACHE #####"
du -sh $C/output/video 2>/dev/null
echo "##### END #####"

#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")/.."
export COMFYUI_PATH="${COMFYUI_PATH:-/home/pwintri2/ComfyUI}"
export COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:8188}"
python3 server.py --host 127.0.0.1 --port "${DREAMWEAVER_PORT:-8788}" --open-browser

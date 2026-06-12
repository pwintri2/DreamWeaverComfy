#!/usr/bin/env bash
# Dreamweaver Comfy desktop launcher.
# The Tauri binary starts and stops its own Python backend on a free localhost port.
set -u

APP_DIR="/home/pwintri2/DreamweaverComfy"
BIN="${APP_DIR}/src-tauri/target/release/dreamweaver-comfy"
export COMFYUI_PATH="${COMFYUI_PATH:-/home/pwintri2/ComfyUI}"
export COMFYUI_URL="${COMFYUI_URL:-http://127.0.0.1:8188}"

if [ -x "${BIN}" ]; then
  "${BIN}"
else
  echo "Dreamweaver: Tauri binary is nog niet gebouwd. Run: npm run tauri:build" >&2
  exit 1
fi

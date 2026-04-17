#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/app"
RUNPOD_WORKSPACE="/workspace"
PERSIST_BASE="${RUNPOD_PERSIST_BASE:-${RUNPOD_WORKSPACE}/chatterbox-lite-runpod}"

link_persistent_path() {
  local app_path="$1"
  local persist_path="$2"

  mkdir -p "$(dirname "$persist_path")"

  if [ -e "$app_path" ] || [ -L "$app_path" ]; then
    rm -rf "$app_path"
  fi

  ln -sfn "$persist_path" "$app_path"
}

mkdir -p "$APP_DIR/model_cache" "$APP_DIR/reference_audio" "$APP_DIR/outputs" "$APP_DIR/logs" "$APP_DIR/hf_cache" "$APP_DIR/voices"

if [ -d "$RUNPOD_WORKSPACE" ]; then
  mkdir -p "$PERSIST_BASE"

  for dir_name in model_cache reference_audio outputs logs hf_cache; do
    mkdir -p "$PERSIST_BASE/$dir_name"
    link_persistent_path "$APP_DIR/$dir_name" "$PERSIST_BASE/$dir_name"
  done

  if [ ! -f "$PERSIST_BASE/config.yaml" ]; then
    cp "$APP_DIR/config.yaml" "$PERSIST_BASE/config.yaml"
  fi

  link_persistent_path "$APP_DIR/config.yaml" "$PERSIST_BASE/config.yaml"
  export HF_HOME="$APP_DIR/hf_cache"
fi

exec "$@"

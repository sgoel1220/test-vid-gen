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
mkdir -p "$APP_DIR/outputs/persistence_outbox"

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

# ---------------------------------------------------------------------------
# Twingate headless client
# Only started when TWINGATE_SERVICE_KEY is set (JSON content of service key).
# New env vars for RunPod template:
#   TWINGATE_NETWORK    — your Twingate network name
#   TWINGATE_SERVICE_KEY — JSON content of the service account key
#   METADATA_API_URL    — http(s):// URL of the metadata server (via Twingate)
#   METADATA_API_KEY    — API key for the metadata server
# ---------------------------------------------------------------------------

start_twingate() {
  if [ -z "${TWINGATE_SERVICE_KEY:-}" ]; then
    return 0
  fi

  local key_file="/tmp/twingate_sk.json"
  printf '%s' "$TWINGATE_SERVICE_KEY" > "$key_file"

  echo "[entrypoint] Starting Twingate headless client..."
  # Headless service-account mode.
  # Verify the exact subcommand against current Twingate docs:
  # https://www.twingate.com/docs/linux-headless
  twingate service-account start --service-key "$key_file" &

  # Wait up to 30 s for the metadata server to become reachable.
  if [ -n "${METADATA_API_URL:-}" ]; then
    local deadline=$(( SECONDS + 30 ))
    while [ "$SECONDS" -lt "$deadline" ]; do
      if curl --max-time 2 -fsS "${METADATA_API_URL}/healthz" >/dev/null 2>&1; then
        echo "[entrypoint] Twingate tunnel up — metadata server reachable"
        return 0
      fi
      sleep 2
    done
    echo "[entrypoint] WARNING: Twingate not ready after 30 s; persistence will use outbox"
  fi
}

start_twingate

exec "$@"

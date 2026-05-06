#!/usr/bin/env bash
# Start the ComfyUI + FastAPI model server in the background, then run the PyWorker.
# Vast.ai serverless expects the PyWorker to be the foreground process (PID 1).
#
# Uses the base image's venv at /venv/main/ for all Python deps.

set -euo pipefail

# Activate the Vast.ai base image venv
source /venv/main/bin/activate

LOG_DIR="/var/log/portal"
LOG_FILE="${LOG_DIR}/server.log"

mkdir -p "$LOG_DIR"

echo "Starting ComfyUI model server on port 8006..."
python /app/server.py >> "$LOG_FILE" 2>&1 &
MODEL_PID=$!

echo "Model server PID: $MODEL_PID"
echo "Logs: $LOG_FILE"

# Give the server a moment to bind the port before the PyWorker starts
sleep 2

echo "Starting PyWorker..."
exec python /app/worker.py

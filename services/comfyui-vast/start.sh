#!/usr/bin/env bash
# Start the ComfyUI + FastAPI server directly (no PyWorker).
# For regular Vast.ai instances — the server IS the foreground process.

set -euo pipefail

echo "Starting ComfyUI model server on port 8006..."
exec python /app/server.py

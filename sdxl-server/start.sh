#!/bin/bash
set -e

mkdir -p /models /loras

# Download + model load now happens inside the app's lifespan
# so uvicorn starts immediately and /health responds right away.
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

#!/usr/bin/env bash
set -euo pipefail

source /venv/bin/activate
exec python /app/server.py

#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "Running alembic upgrade head..."
    alembic upgrade head
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8006

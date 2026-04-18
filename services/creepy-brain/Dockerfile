# Stage 1: Build dashboard frontend
FROM node:20-slim AS frontend
WORKDIR /build
COPY services/creepy-brain/package.json services/creepy-brain/package-lock.json* ./
RUN npm ci --ignore-scripts
COPY services/creepy-brain/static/src/ ./static/src/
RUN npx esbuild static/src/main.ts --bundle --outfile=static/dist/app.js --format=esm --target=es2020

# Stage 2: Python runtime
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (ffmpeg for audio/video encoding)
RUN apt-get update && apt-get install -y \
    gcc \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY services/creepy-brain/pyproject.toml ./

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Copy application code
COPY services/creepy-brain/app/ ./app/
COPY services/creepy-brain/alembic/ ./alembic/
COPY services/creepy-brain/alembic.ini ./
COPY services/creepy-brain/entrypoint.sh ./
COPY services/creepy-brain/static/dashboard.html services/creepy-brain/static/index.html services/creepy-brain/static/full_story.wav* ./static/

# Copy built frontend from stage 1
COPY --from=frontend /build/static/dist/app.js ./static/dist/app.js

RUN chmod +x entrypoint.sh

# Expose the application port
EXPOSE 8006

# RUN_MIGRATIONS=true (default): run alembic upgrade head before uvicorn.
# Set RUN_MIGRATIONS=false to skip (e.g. migrations run as a separate job in prod).
ENTRYPOINT ["./entrypoint.sh"]

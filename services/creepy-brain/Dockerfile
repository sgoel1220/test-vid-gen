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

# Build dashboard frontend
COPY services/creepy-brain/package.json services/creepy-brain/package-lock.json* ./
COPY services/creepy-brain/static/src/ ./static/src/
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm ci --ignore-scripts \
    && npm run build \
    && rm -rf node_modules package.json package-lock.json static/src \
    && apt-get purge -y nodejs npm && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY services/creepy-brain/app/ ./app/
COPY services/creepy-brain/alembic/ ./alembic/
COPY services/creepy-brain/alembic.ini ./
COPY services/creepy-brain/entrypoint.sh ./
COPY services/creepy-brain/static/dashboard.html services/creepy-brain/static/index.html services/creepy-brain/static/full_story.wav* ./static/

RUN chmod +x entrypoint.sh

# Expose the application port
EXPOSE 8006

# RUN_MIGRATIONS=true (default): run alembic upgrade head before uvicorn.
# Set RUN_MIGRATIONS=false to skip (e.g. migrations run as a separate job in prod).
ENTRYPOINT ["./entrypoint.sh"]

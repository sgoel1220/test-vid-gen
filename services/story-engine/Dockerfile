# Build context: Chatterbox-TTS-Server/ (repo root)
# Build command (from Chatterbox-TTS-Server/):
#   docker buildx build --platform linux/amd64 \
#     -f services/story-engine/Dockerfile \
#     -t shubh67678/story-engine:latest --push .

FROM python:3.11-slim AS base

WORKDIR /srv

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install protocol package first (rarely changes -> good cache layer).
COPY creepy_pasta_protocol /srv/creepy_pasta_protocol
RUN pip install --no-cache-dir /srv/creepy_pasta_protocol

# Install app dependencies.
COPY services/story-engine/pyproject.toml /srv/story-engine/pyproject.toml
WORKDIR /srv/story-engine
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e .

# Copy app source.
COPY services/story-engine/ /srv/story-engine/

EXPOSE 8081

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]

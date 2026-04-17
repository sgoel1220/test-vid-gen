# Minimal TTS server for creepy-brain orchestration
# Exposes only /synthesize endpoint - no UI, no orchestration logic
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     -f services/tts-server/Dockerfile \
#     -t ghcr.io/sgoel1220/tts-server:main --push .

FROM docker.io/nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV HF_HOME=/app/hf_cache
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    git \
    libsndfile1 \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /app

# Install minimal Python dependencies
COPY services/tts-server/requirements-minimal.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r /tmp/requirements.txt && \
    python -m pip install --no-cache-dir --no-deps git+https://github.com/devnen/chatterbox-v2.git@cc0357396d9c73fc1e6c544ee40bb596020edd09 s3tokenizer==0.3.0 onnx==1.16.0 && \
    python -m pip install --no-cache-dir "protobuf>=4.25.0"

# Copy only the minimal server
COPY services/tts-server/minimal_server.py /app/minimal_server.py

# Create required directories
RUN mkdir -p /app/model_cache /app/reference_audio /app/hf_cache

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8005/health >/dev/null || exit 1

CMD ["python", "minimal_server.py"]

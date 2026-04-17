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

# Install uv for faster package installs (10-100x faster than pip)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Install Python dependencies with uv
COPY services/tts-server/requirements-minimal.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt && \
    uv pip install --system --no-cache --no-deps git+https://github.com/devnen/chatterbox-v2.git@cc0357396d9c73fc1e6c544ee40bb596020edd09 s3tokenizer==0.3.0 onnx==1.16.0 && \
    uv pip install --system --no-cache "protobuf>=4.25.0"

# Copy only the minimal server
COPY services/tts-server/minimal_server.py /app/minimal_server.py

# Create required directories
RUN mkdir -p /app/model_cache /app/hf_cache

# Copy predefined voices
COPY services/tts-server/voices/ /app/reference_audio/

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8005/health >/dev/null || exit 1

CMD ["python", "minimal_server.py"]

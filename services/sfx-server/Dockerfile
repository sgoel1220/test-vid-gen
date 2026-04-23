# SFX generation server using EzAudio
# Exposes /generate endpoint on port 8008
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     -f services/sfx-server/Dockerfile \
#     -t ghcr.io/sgoel1220/sfx-server:main --push .

FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV HF_HOME=/app/hf_cache
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    git \
    libsndfile1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster package installs
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Clone EzAudio pinned to a reviewed commit for reproducible builds.
# Update this hash when intentionally upgrading EzAudio.
ARG EZAUDIO_COMMIT=86f18268117dde73bb274401c610e639e4a8fae1
RUN git clone https://github.com/haidog-yaqub/EzAudio.git /app/ezaudio \
    && git -C /app/ezaudio checkout "$EZAUDIO_COMMIT"

# Install EzAudio dependencies
RUN uv pip install --system --no-cache -r /app/ezaudio/requirements.txt

# Install remaining server dependencies
COPY services/sfx-server/requirements.txt /app/requirements.txt
RUN uv pip install --system --no-cache -r /app/requirements.txt

# Copy server
COPY services/sfx-server/server.py /app/server.py

# Add EzAudio to Python path so `from api.ezaudio import EzAudio` works
ENV PYTHONPATH="/app/ezaudio:${PYTHONPATH}"

# Create required directories
RUN mkdir -p /app/hf_cache

EXPOSE 8008

# Probe /ready (503 until model loaded) so the container is only marked
# healthy when EzAudio is fully initialized.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8008/ready >/dev/null || exit 1

CMD ["python", "/app/server.py"]

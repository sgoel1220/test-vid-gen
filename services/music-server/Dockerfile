# Music generation server using ACE-Step 1.5
# Exposes /generate and /outpaint endpoints on port 8007
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     -f services/music-server/Dockerfile \
#     -t ghcr.io/sgoel1220/music-server:main --push .

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

# Clone ACE-Step 1.5 pinned to a reviewed commit for reproducible builds.
# Update this hash when intentionally upgrading ACE-Step.
ARG ACE_STEP_COMMIT=d61c7acb9565624de77d88415195c11823d1ec0d
RUN git clone https://github.com/ACE-Step/ACE-Step-1.5.git /app/ace-step \
    && git -C /app/ace-step checkout "$ACE_STEP_COMMIT"

# Install nano-vllm first (bundled LLM backend; must precede ace-step install)
RUN uv pip install --system --no-cache -e /app/ace-step/acestep/third_parts/nano-vllm

# Install ACE-Step 1.5
RUN uv pip install --system --no-cache -e /app/ace-step

# Install remaining server dependencies
COPY services/music-server/requirements.txt /app/requirements.txt
RUN uv pip install --system --no-cache -r /app/requirements.txt

# Copy server
COPY services/music-server/server.py /app/server.py

# Create required directories
RUN mkdir -p /app/hf_cache

EXPOSE 8007

# Probe /ready (503 until models loaded) so the container is only marked
# healthy when the DiT + LLM are fully initialized.
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8007/ready >/dev/null || exit 1

CMD ["python", "/app/server.py"]

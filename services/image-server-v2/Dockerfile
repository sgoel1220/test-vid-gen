# Image Server v2: SDXL 1.0 + Impressionism LoRA + Lightning 4-step
# ~8 GB VRAM peak, fits RTX A4000 (16 GB) with headroom.
#
# Models are downloaded at container startup (not baked in) — matches v1 arch.
# Pass CIVITAI_TOKEN env var at runtime for the Impressionism LoRA.
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     -f services/image-server-v2/Dockerfile \
#     -t ghcr.io/sgoel1220/image-server-v2:main --push .

# python:3.11-slim + pip torch (~3.5 GB) vs pytorch/pytorch base (~9 GB)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV HF_HOME=/app/hf_cache
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# PyTorch with bundled CUDA 12.1 runtime (replaces the pytorch/pytorch base image)
RUN uv pip install --system --no-cache \
    "torch==2.5.1+cu121" \
    --index-url https://download.pytorch.org/whl/cu121

# Remaining dependencies
RUN uv pip install --system --no-cache \
    diffusers==0.37.1 \
    transformers==4.51.3 \
    accelerate==1.6.0 \
    safetensors==0.5.3 \
    Pillow==11.1.0 \
    huggingface_hub==0.34.1 \
    hf_transfer==0.1.9 \
    fastapi==0.115.6 \
    "uvicorn[standard]==0.32.1" \
    pydantic==2.10.4

RUN mkdir -p /app/hf_cache /app/loras

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

COPY services/image-server-v2/server.py /app/server.py

EXPOSE 8006

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8006/ready >/dev/null || exit 1

CMD ["python", "server.py"]

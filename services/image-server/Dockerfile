# Minimal Z-Image generation server
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     -f services/image-server/Dockerfile \
#     -t ghcr.io/sgoel1220/image-server:main --push .

FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

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

# Pin versions — stable layer, cached across rebuilds unless deps change
RUN uv pip install --system --no-cache \
    diffusers==0.33.1 \
    transformers==4.46.3 \
    accelerate==1.6.0 \
    safetensors==0.5.3 \
    Pillow==11.1.0 \
    huggingface_hub==0.28.1 \
    hf_transfer==0.1.4 \
    fastapi==0.115.6 \
    "uvicorn[standard]==0.32.1" \
    pydantic==2.10.4

RUN mkdir -p /app/hf_cache

# Copy app last — changes here don't invalidate the dep layer above
COPY services/image-server/server.py /app/server.py

EXPOSE 8006

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8006/health >/dev/null || exit 1

CMD ["python", "server.py"]

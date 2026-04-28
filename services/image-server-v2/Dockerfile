# Image Server v2: SDXL 1.0 + Impressionism LoRA + Lightning 4-step
# ~8 GB VRAM peak, fits RTX A4000 (16 GB) with headroom.
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     --build-arg CIVITAI_TOKEN=$CIVITAI_TOKEN \
#     -f services/image-server-v2/Dockerfile \
#     -t ghcr.io/sgoel1220/image-server-v2:main --push .

FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV HF_HOME=/app/hf_cache
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

ARG CIVITAI_TOKEN=""

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Python dependencies
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
    pydantic==2.10.4 \
    xformers==0.0.29.post3

RUN mkdir -p /app/hf_cache /app/loras

# ---------------------------------------------------------------------------
# Bake models into image (avoids download on every cold start)
# ---------------------------------------------------------------------------

# SDXL 1.0 base (fp16 variant, ~6.5 GB)
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('stabilityai/stable-diffusion-xl-base-1.0', allow_patterns=['*.fp16.safetensors', '*.json', '*.txt', 'tokenizer/*', 'tokenizer_2/*'])"

# VAE: fp16-fix
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('madebyollin/sdxl-vae-fp16-fix')"

# Lightning 4-step LoRA
RUN python -c "from huggingface_hub import hf_hub_download; hf_hub_download('ByteDance/SDXL-Lightning', 'sdxl_lightning_4step_lora.safetensors')"

# Impressionism LoRA from CivitAI (55 MB)
RUN curl -L -o /app/loras/impressionism_sdxl.safetensors \
    "https://civitai.com/api/download/models/133465?token=${CIVITAI_TOKEN}"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

COPY services/image-server-v2/server.py /app/server.py

EXPOSE 8006

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8006/ready >/dev/null || exit 1

CMD ["python", "server.py"]

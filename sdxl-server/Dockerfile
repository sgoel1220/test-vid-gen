FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

# ── System dependencies ────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common \
        curl \
        git \
        libgl1-mesa-glx \
        libglib2.0-0 \
        libsm6 \
        libxrender1 \
        libxext6 \
    && add-apt-repository ppa:deadsnakes/ppa -y \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3.11-distutils \
    && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 \
    && ln -sf /usr/bin/python3.11 /usr/local/bin/python3 \
    && ln -sf /usr/local/bin/pip /usr/local/bin/pip3 \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────
# Install torch first (CUDA 12.1 wheel) so diffusers picks up the right build
RUN pip3 install --no-cache-dir \
    torch==2.3.0 torchvision==0.18.0 torchaudio==2.3.0 \
    --index-url https://download.pytorch.org/whl/cu121

# xformers wheel built against torch 2.3 / cu121
RUN pip3 install --no-cache-dir \
    xformers==0.0.26.post1 \
    --index-url https://download.pytorch.org/whl/cu121

RUN pip3 install --no-cache-dir \
    diffusers==0.29.2 \
    transformers==4.41.2 \
    accelerate==0.30.1 \
    safetensors==0.4.3 \
    Pillow==10.3.0 \
    fastapi==0.111.0 \
    "uvicorn[standard]==0.30.1" \
    pydantic==2.7.1

# ── App ───────────────────────────────────────────────────────────────────
WORKDIR /app
COPY sdxl-server/app/ /app/
COPY sdxl-server/start.sh /start.sh
RUN chmod +x /start.sh

RUN mkdir -p /models /loras /outputs

EXPOSE 8000

# ── Model defaults (no env vars needed at runtime) ────────────────────────
# Juggernaut XL Ragnarok  — civitai.com/models/133005
ENV BASE_CIVITAI_ID=1759168
# ClassipeintXL v2.1 + Eldritch Impressionism v1.5
ENV LORA_CIVITAI_IDS="ClassipeintXL_v2.1:356771,Eldritch_Impressionism:360775"

# Warm-cache: allows HuggingFace model files to be baked in at build time
# if BUILD_MODELS=1 is passed. Skip in fast builds.
ARG BUILD_MODELS=0
RUN if [ "$BUILD_MODELS" = "1" ] && [ -n "$HF_TOKEN" ]; then \
        python3 -c "\
from diffusers import StableDiffusionXLPipeline, StableDiffusionXLImg2ImgPipeline; \
import torch, os; \
tok=os.environ['HF_TOKEN']; \
StableDiffusionXLPipeline.from_pretrained('stabilityai/stable-diffusion-xl-base-1.0', torch_dtype=torch.float16, variant='fp16', token=tok); \
StableDiffusionXLImg2ImgPipeline.from_pretrained('stabilityai/stable-diffusion-xl-refiner-1.0', torch_dtype=torch.float16, variant='fp16', token=tok); \
print('Models cached.')"; \
    fi

CMD ["/start.sh"]

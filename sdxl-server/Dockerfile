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
RUN pip3 install --no-cache-dir \
    torch==2.3.0 torchvision==0.18.0 torchaudio==2.3.0 \
    --index-url https://download.pytorch.org/whl/cu121

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
    pydantic==2.7.1 \
    requests==2.32.3

# ── App ───────────────────────────────────────────────────────────────────
WORKDIR /app
COPY sdxl-server/app/ /app/
COPY sdxl-server/start.sh /start.sh
RUN chmod +x /start.sh

RUN mkdir -p /models /outputs

EXPOSE 8000

# ── Base model: Juggernaut XL Ragnarok (downloaded at first boot) ─────────
ENV BASE_CIVITAI_ID=1759168

HEALTHCHECK --interval=30s --timeout=10s --start-period=600s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health >/dev/null || exit 1

CMD ["/start.sh"]

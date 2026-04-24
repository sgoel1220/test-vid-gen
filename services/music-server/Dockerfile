# Music generation server using ACE-Step 1.5
# Exposes /generate and /outpaint endpoints on port 8007
#
# Build (from repo root):
#   docker buildx build --platform linux/amd64 \
#     -f services/music-server/Dockerfile \
#     -t ghcr.io/sgoel1220/music-server:main --push .

# Use CUDA 12.8 base to match ACE-Step's torch==2.10.0+cu128 requirement.
# Avoids installing a second torch on top of the old pytorch/pytorch base image.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
ENV HF_HOME=/app/hf_cache
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# Install system deps + Python 3.11 (ace-step requires >=3.11,<3.13)
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    git \
    libsndfile1 \
    python3.11 \
    python3.11-dev \
    python3.11-distutils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set python3.11 as the default python/python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

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

# Install ACE-Step 1.5 (installs torch 2.10.0+cu128 from pytorch-cu128 index)
RUN uv pip install --system --no-cache -e /app/ace-step

# Patch: make LoKRConfig import lazy in lora/lifecycle.py.
# That import pulls in lightning → torchmetrics → torchvision which triggers a
# circular-import crash on this torch/torchvision version combo.
# LoKRConfig is only used for LoRA *training*; the inference server never calls it.
RUN python3 - <<'EOF'
import pathlib, textwrap
p = pathlib.Path("/app/ace-step/acestep/core/generation/handler/lora/lifecycle.py")
src = p.read_text()
src = src.replace(
    "from acestep.training.configs import LoKRConfig",
    textwrap.dedent("""\
        try:
            from acestep.training.configs import LoKRConfig
        except Exception:
            LoKRConfig = None  # type: ignore[assignment,misc]  # training deps unavailable"""),
)
p.write_text(src)
EOF

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

# Build context: Chatterbox-TTS-Server/ (repo root)
# Build command (from Chatterbox-TTS-Server/):
#   docker buildx build --platform linux/amd64 \
#     -f services/tts-server/Dockerfile \
#     -t shubh67678/chatterbox-tts-server:latest --push .

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

# Install Twingate headless client
# See https://www.twingate.com/docs/client-linux for the current install method.
RUN curl -fsSL https://binaries.twingate.com/client/linux/install.sh | bash

RUN ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /app

COPY services/tts-server/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY services/tts-server/requirements.txt /tmp/requirements.txt

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r /tmp/requirements.txt && \
    python -m pip install --no-cache-dir --no-deps git+https://github.com/devnen/chatterbox-v2.git@cc0357396d9c73fc1e6c544ee40bb596020edd09 s3tokenizer==0.3.0 onnx==1.16.0 && \
    python -m pip install --no-cache-dir "protobuf>=4.25.0" && \
    python -m pip install --no-cache-dir git+https://github.com/huggingface/diffusers@29b91098f68847f4e50d099735532c7d5735b17e

COPY services/tts-server/config.yaml /app/config.yaml
COPY services/tts-server/app.py services/tts-server/config.py services/tts-server/cpu_runtime.py services/tts-server/engine.py services/tts-server/enums.py services/tts-server/files.py services/tts-server/image_routes.py services/tts-server/job_store.py services/tts-server/lite_clone_server.py services/tts-server/models.py services/tts-server/routes.py services/tts-server/run_orchestrator.py services/tts-server/utils.py /app/
COPY services/tts-server/audio /app/audio
COPY services/tts-server/text /app/text
COPY services/tts-server/image /app/image
COPY services/tts-server/lite_ui /app/lite_ui
COPY services/tts-server/persistence /app/persistence

RUN mkdir -p /app/model_cache /app/reference_audio /app/outputs /app/logs /app/hf_cache /app/voices

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8005/api/model-info >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "lite_clone_server.py"]

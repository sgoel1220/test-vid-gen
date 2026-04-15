# Build context: creepy_pasta/ (parent of Chatterbox-TTS-Server/)
# Build command (from creepy_pasta/):
#   docker buildx build --platform linux/amd64 \
#     -f Chatterbox-TTS-Server/lite_runpod/Dockerfile \
#     -t shubh67678/chatterbox-lite-runpod:latest --push .

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

COPY Chatterbox-TTS-Server/lite_runpod/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY Chatterbox-TTS-Server/lite_runpod/requirements.txt /tmp/requirements.txt

# Install creepy_pasta_protocol (sibling package, requires parent build context)
COPY creepy_pasta_protocol /tmp/creepy_pasta_protocol
RUN python -m pip install --no-cache-dir -e /tmp/creepy_pasta_protocol

RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r /tmp/requirements.txt && \
    python -m pip install --no-cache-dir --no-deps git+https://github.com/devnen/chatterbox-v2.git@cc0357396d9c73fc1e6c544ee40bb596020edd09 s3tokenizer==0.3.0 onnx==1.16.0 && \
    python -m pip install --no-cache-dir "protobuf>=4.25.0" && \
    python -m pip install --no-cache-dir git+https://github.com/huggingface/diffusers

COPY Chatterbox-TTS-Server/lite_runpod/config.yaml /app/config.yaml
COPY Chatterbox-TTS-Server/app.py Chatterbox-TTS-Server/config.py Chatterbox-TTS-Server/cpu_runtime.py Chatterbox-TTS-Server/engine.py Chatterbox-TTS-Server/enums.py Chatterbox-TTS-Server/files.py Chatterbox-TTS-Server/image_routes.py Chatterbox-TTS-Server/job_store.py Chatterbox-TTS-Server/lite_clone_server.py Chatterbox-TTS-Server/models.py Chatterbox-TTS-Server/routes.py Chatterbox-TTS-Server/routes_history.py Chatterbox-TTS-Server/run_orchestrator.py Chatterbox-TTS-Server/utils.py /app/
COPY Chatterbox-TTS-Server/audio /app/audio
COPY Chatterbox-TTS-Server/text /app/text
COPY Chatterbox-TTS-Server/image /app/image
COPY Chatterbox-TTS-Server/lite_ui /app/lite_ui
COPY Chatterbox-TTS-Server/persistence /app/persistence

RUN mkdir -p /app/model_cache /app/reference_audio /app/outputs /app/logs /app/hf_cache /app/voices

EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8005/api/model-info >/dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "lite_clone_server.py"]

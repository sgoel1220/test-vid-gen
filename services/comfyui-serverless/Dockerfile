# ComfyUI Serverless Worker for RunPod
# Stack: SDXL 1.0 + Impressionism LoRA + Lightning 4-step LoRA + FP16 VAE
#
# Build:
#   docker build --build-arg CIVITAI_TOKEN=xxx -t comfyui-serverless:latest .
#
# Push (example):
#   docker tag comfyui-serverless:latest ghcr.io/sgoel1220/comfyui-serverless:latest
#   docker push ghcr.io/sgoel1220/comfyui-serverless:latest

FROM runpod/worker-comfyui:5.8.5-base

# CivitAI token for authenticated downloads (passed as build arg, not baked into image)
ARG CIVITAI_TOKEN=""

# ---------------------------------------------------------------------------
# Base model: SDXL 1.0 (fp16 variant, ~6.5 GB)
# ---------------------------------------------------------------------------
RUN comfy model download \
    --url "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0_0.9vae.safetensors" \
    --relative-path models/checkpoints \
    --filename sd_xl_base_1.0.safetensors

# ---------------------------------------------------------------------------
# VAE: madebyollin/sdxl-vae-fp16-fix (prevents black images in fp16)
# ---------------------------------------------------------------------------
RUN comfy model download \
    --url "https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors" \
    --relative-path models/vae \
    --filename sdxl_vae_fp16_fix.safetensors

# ---------------------------------------------------------------------------
# Style LoRA: Impressionism SDXL (CivitAI model 122535, version 133465, ~55 MB)
# Trained on Monet, Renoir, Degas, Cézanne, Van Gogh, Pissarro, Sargent,
# Gauguin, Matisse, Hassam
# ---------------------------------------------------------------------------
RUN comfy model download \
    --url "https://civitai.com/api/download/models/133465?token=${CIVITAI_TOKEN}" \
    --relative-path models/loras \
    --filename impressionism_sdxl.safetensors

# ---------------------------------------------------------------------------
# Speed LoRA: SDXL-Lightning 4-step LoRA (ByteDance, ~394 MB)
# Enables high-quality generation in just 4 inference steps
# ---------------------------------------------------------------------------
RUN comfy model download \
    --url "https://huggingface.co/ByteDance/SDXL-Lightning/resolve/main/sdxl_lightning_4step_lora.safetensors" \
    --relative-path models/loras \
    --filename sdxl_lightning_4step_lora.safetensors

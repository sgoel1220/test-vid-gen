"""SDXL Impressionist image server — POST a prompt, get a PNG back.

Stack:
  - Base: SDXL 1.0 (stabilityai/stable-diffusion-xl-base-1.0)
  - Style: Impressionism SDXL LoRA (CivitAI 133465, strength 0.8)
  - Speed: SDXL-Lightning 4-step LoRA (ByteDance, strength 1.0)
  - VAE: madebyollin/sdxl-vae-fp16-fix
  - Scheduler: Euler, sgm_uniform spacing
  - 4 steps, cfg 2.0

~7-8 GB VRAM peak. Fits RTX A4000 (16 GB) comfortably.
"""

from __future__ import annotations

import gc
import io
import logging
import os
import time
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import torch
from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline, AutoencoderKL
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_BASE_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
_VAE_MODEL = "madebyollin/sdxl-vae-fp16-fix"
_LIGHTNING_REPO = "ByteDance/SDXL-Lightning"
_LIGHTNING_LORA = "sdxl_lightning_4step_lora.safetensors"

# Impressionism LoRA — downloaded at build time to /app/loras/
_IMPRESSIONISM_LORA_PATH = os.getenv(
    "IMPRESSIONISM_LORA_PATH", "/app/loras/impressionism_sdxl.safetensors"
)
_IMPRESSIONISM_STRENGTH = float(os.getenv("IMPRESSIONISM_STRENGTH", "0.8"))
_CIVITAI_TOKEN = os.getenv("CIVITAI_TOKEN", "")

_pipe: StableDiffusionXLPipeline | None = None

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load() -> StableDiffusionXLPipeline:
    global _pipe
    if _pipe is not None:
        return _pipe

    logger.info("Loading SDXL 1.0 base + fp16 VAE...")

    # Load VAE separately (fp16-safe; downloads from HF on first run)
    vae = AutoencoderKL.from_pretrained(
        _VAE_MODEL,
        torch_dtype=torch.float16,
    )

    # Load base pipeline on CPU first — move to CUDA only after LoRA fusion
    # to avoid VRAM spike (base 7 GB + two LoRA adapters overhead).
    _pipe = StableDiffusionXLPipeline.from_pretrained(
        _BASE_MODEL,
        vae=vae,
        torch_dtype=torch.float16,
        variant="fp16",
    )

    # Download Impressionism LoRA from CivitAI if not already cached
    if not os.path.exists(_IMPRESSIONISM_LORA_PATH):
        if not _CIVITAI_TOKEN:
            raise RuntimeError(
                "CIVITAI_TOKEN env var required to download Impressionism LoRA"
            )
        logger.info("Downloading Impressionism LoRA from CivitAI...")
        os.makedirs(os.path.dirname(_IMPRESSIONISM_LORA_PATH), exist_ok=True)
        url = f"https://civitai.com/api/download/models/133465?token={_CIVITAI_TOKEN}"
        urllib.request.urlretrieve(url, _IMPRESSIONISM_LORA_PATH)
        logger.info("Impressionism LoRA saved to %s", _IMPRESSIONISM_LORA_PATH)

    # Load Impressionism style LoRA
    logger.info("Loading Impressionism LoRA (strength=%.2f)...", _IMPRESSIONISM_STRENGTH)
    _pipe.load_lora_weights(
        _IMPRESSIONISM_LORA_PATH,
        adapter_name="impressionism",
    )

    # Load Lightning speed LoRA (downloads from HF on first run)
    logger.info("Loading SDXL-Lightning 4-step LoRA...")
    lightning_path = hf_hub_download(_LIGHTNING_REPO, _LIGHTNING_LORA)
    _pipe.load_lora_weights(
        lightning_path,
        adapter_name="lightning",
    )

    # Set LoRA weights and fuse for performance
    _pipe.set_adapters(
        ["impressionism", "lightning"],
        adapter_weights=[_IMPRESSIONISM_STRENGTH, 1.0],
    )
    _pipe.fuse_lora()
    _pipe.unload_lora_weights()  # Free LoRA memory after fusing

    # Move fused pipeline to CUDA in one shot — single peak, no double-load
    _pipe = _pipe.to("cuda")

    # Scheduler: Euler with sgm_uniform (Lightning-optimal)
    _pipe.scheduler = EulerDiscreteScheduler.from_config(
        _pipe.scheduler.config,
        timestep_spacing="trailing",
    )

    # Memory optimizations (SDPA is default in PyTorch 2.x + diffusers)
    _pipe.set_progress_bar_config(disable=True)

    # Clear loading overhead
    torch.cuda.empty_cache()
    gc.collect()

    vram_gb = torch.cuda.memory_allocated() / 1024**3
    logger.info("Pipeline ready. VRAM: %.2f GB", vram_gb)

    return _pipe


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not torch.cuda.is_available():
        logger.error("CUDA not available — cannot serve. Check GPU/driver.")
        # Don't crash; start server so /health can report the problem
        yield
        return
    try:
        _load()
    except Exception:
        logger.exception("Model loading failed — server will start but /ready returns 503")
    yield


app = FastAPI(
    title="Image Server v2",
    description="SDXL + Impressionism LoRA + Lightning 4-step. POST /generate → PNG.",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image prompt.")
    negative_prompt: str = Field(
        "photorealistic, photograph, blurry, low quality, watermark, text, deformed",
        description="Negative prompt.",
    )
    width: int = Field(1280, ge=512, le=1536)
    height: int = Field(720, ge=512, le=1536)
    steps: int = Field(4, ge=1, le=8)
    guidance_scale: float = Field(2.0, ge=0.0, le=5.0)
    seed: int | None = Field(None, ge=0)


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    vram_gb: float | None = None


class ReadyResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> HealthResponse:
    vram: float | None = None
    if torch.cuda.is_available():
        vram = round(torch.cuda.memory_allocated() / 1024**3, 2)
    return HealthResponse(status="ok", model_loaded=_pipe is not None, vram_gb=vram)


@app.get("/ready")
def ready() -> ReadyResponse:
    if _pipe is not None:
        return ReadyResponse(status="ready")
    raise HTTPException(status_code=503, detail="Model loading")


@app.post("/generate", response_class=Response)
def generate(request: GenerateRequest) -> Response:
    """Generate an impressionist painting and return it as PNG bytes."""
    pipe = _load()

    seed = request.seed if request.seed is not None else int(torch.randint(0, 2**32, (1,)).item())
    generator = torch.Generator(device="cuda").manual_seed(seed)

    logger.info(
        "Generating: steps=%d size=%dx%d cfg=%.1f seed=%d",
        request.steps,
        request.width,
        request.height,
        request.guidance_scale,
        seed,
    )

    t0 = time.perf_counter()

    # Clear VRAM fragmentation
    torch.cuda.empty_cache()

    try:
        with torch.inference_mode():
            result = pipe(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                num_inference_steps=request.steps,
                guidance_scale=request.guidance_scale,
                generator=generator,
            )
    except RuntimeError as exc:
        torch.cuda.empty_cache()
        gc.collect()
        if "out of memory" in str(exc).lower():
            logger.error("CUDA OOM during generation: %s", exc)
            raise HTTPException(status_code=507, detail="GPU out of memory") from exc
        raise

    torch.cuda.empty_cache()

    img = result.images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    elapsed = time.perf_counter() - t0
    logger.info(
        "Done in %.1fs. VRAM: %.2f GB",
        elapsed,
        torch.cuda.memory_allocated() / 1024**3,
    )

    return Response(
        content=buf.read(),
        media_type="image/png",
        headers={"X-Seed": str(seed), "X-Elapsed": f"{elapsed:.2f}"},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8006, reload=False)

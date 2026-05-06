"""SDXL Impressionist image server for Vast.ai Serverless deployment.

Stack:
  - Base: SDXL 1.0 (stabilityai/stable-diffusion-xl-base-1.0)
  - Style: Impressionism SDXL LoRA (CivitAI 133465, strength 0.8)
  - Speed: SDXL-Lightning 4-step LoRA (ByteDance, strength 1.0)
  - VAE: madebyollin/sdxl-vae-fp16-fix
  - Scheduler: Euler, trailing spacing
  - 4 steps, cfg 2.0

~7-8 GB VRAM peak. Fits RTX A4000 (16 GB) comfortably.

This variant is designed for Vast.ai Serverless — the PyWorker proxies
requests to this FastAPI server on localhost:8006.
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
from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLPipeline
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

# Tunable generation defaults (override via env to avoid rebuilds)
_DEFAULT_STEPS = int(os.getenv("DEFAULT_STEPS", "4"))
_DEFAULT_GUIDANCE_SCALE = float(os.getenv("DEFAULT_GUIDANCE_SCALE", "2.0"))
_DEFAULT_WIDTH = int(os.getenv("DEFAULT_WIDTH", "1280"))
_DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", "720"))
_DEFAULT_NEGATIVE_PROMPT = os.getenv(
    "DEFAULT_NEGATIVE_PROMPT",
    "photorealistic, photograph, blurry, low quality, watermark, text, deformed",
)

_pipe: StableDiffusionXLPipeline | None = None

# Runtime loading state — populated by _load()
_load_state: dict[str, object] = {
    "impressionism_loaded": False,
    "lightning_loaded": False,
    "fused": False,
    "load_error": None,
    "load_time_s": None,
}

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load() -> StableDiffusionXLPipeline:
    global _pipe
    if _pipe is not None:
        return _pipe

    _load_state.update(
        impressionism_loaded=False, lightning_loaded=False, fused=False, load_error=None, load_time_s=None
    )
    t_start = time.perf_counter()

    def _elapsed() -> str:
        return f"{time.perf_counter() - t_start:.1f}s"

    try:
        logger.info("[1/7] Loading VAE (madebyollin/sdxl-vae-fp16-fix)...")
        vae = AutoencoderKL.from_pretrained(
            _VAE_MODEL,
            torch_dtype=torch.float16,
        )
        logger.info("[1/7] VAE loaded. (%s)", _elapsed())

        logger.info("[2/7] Loading SDXL 1.0 base pipeline...")
        _pipe = StableDiffusionXLPipeline.from_pretrained(
            _BASE_MODEL,
            vae=vae,
            torch_dtype=torch.float16,
            variant="fp16",
        ).to("cuda")
        logger.info(
            "[2/7] Base pipeline on CUDA. VRAM: %.2f GB (%s)",
            torch.cuda.memory_allocated() / 1024**3,
            _elapsed(),
        )

        # Download Impressionism LoRA from CivitAI if not already cached
        _has_impressionism = os.path.exists(_IMPRESSIONISM_LORA_PATH)
        if not _has_impressionism:
            if _CIVITAI_TOKEN:
                logger.info("[3/7] Downloading Impressionism LoRA from CivitAI...")
                os.makedirs(os.path.dirname(_IMPRESSIONISM_LORA_PATH), exist_ok=True)
                url = f"https://civitai.com/api/download/models/133465?token={_CIVITAI_TOKEN}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(_IMPRESSIONISM_LORA_PATH, "wb") as f:
                    f.write(resp.read())
                _has_impressionism = True
                logger.info("[3/7] Impressionism LoRA downloaded. (%s)", _elapsed())
            else:
                logger.warning(
                    "[3/7] CIVITAI_TOKEN not set — skipping Impressionism LoRA. Running Lightning-only."
                )
        else:
            logger.info("[3/7] Impressionism LoRA already cached, skipping download.")

        if _has_impressionism:
            logger.info("[4/7] Loading Impressionism LoRA (strength=%.2f)...", _IMPRESSIONISM_STRENGTH)
            _pipe.load_lora_weights(
                _IMPRESSIONISM_LORA_PATH,
                adapter_name="impressionism",
            )
            _load_state["impressionism_loaded"] = True
            logger.info("[4/7] Impressionism LoRA loaded. (%s)", _elapsed())
        else:
            logger.info("[4/7] Skipping Impressionism LoRA.")

        logger.info("[5/7] Loading SDXL-Lightning 4-step LoRA...")
        lightning_path = hf_hub_download(_LIGHTNING_REPO, _LIGHTNING_LORA)
        _pipe.load_lora_weights(
            lightning_path,
            adapter_name="lightning",
        )
        _load_state["lightning_loaded"] = True
        logger.info("[5/7] Lightning LoRA loaded. (%s)", _elapsed())

        if _has_impressionism:
            logger.info("[6/7] Fusing LoRAs (impressionism=%.2f, lightning=1.0)...", _IMPRESSIONISM_STRENGTH)
            _pipe.set_adapters(
                ["impressionism", "lightning"],
                adapter_weights=[_IMPRESSIONISM_STRENGTH, 1.0],
            )
        else:
            logger.info("[6/7] Fusing Lightning LoRA only...")
            _pipe.set_adapters(["lightning"], adapter_weights=[1.0])
        _pipe.fuse_lora()
        _pipe.unload_lora_weights()
        _load_state["fused"] = True
        logger.info(
            "[6/7] LoRAs fused and unloaded. VRAM: %.2f GB (%s)",
            torch.cuda.memory_allocated() / 1024**3,
            _elapsed(),
        )

        logger.info("[7/7] Configuring scheduler and finalizing...")
        _pipe.scheduler = EulerDiscreteScheduler.from_config(
            _pipe.scheduler.config,
            timestep_spacing="trailing",
        )
        _pipe.set_progress_bar_config(disable=True)
        torch.cuda.empty_cache()
        gc.collect()

        _load_state["load_time_s"] = round(time.perf_counter() - t_start, 1)
        vram_gb = torch.cuda.memory_allocated() / 1024**3
        # This log line is monitored by the PyWorker's LogActionConfig
        logger.info("Application startup complete. VRAM: %.2f GB — total startup: %s", vram_gb, _elapsed())

    except Exception as exc:
        _load_state["load_error"] = str(exc)
        logger.exception("Pipeline loading failed")
        raise

    return _pipe


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not torch.cuda.is_available():
        logger.error("CUDA not available — cannot serve. Check GPU/driver.")
        yield
        return
    try:
        _load()
    except Exception:
        logger.exception("Model loading failed — server will start but /ready returns 503")
    yield


app = FastAPI(
    title="Image Server v2 (Serverless)",
    description="SDXL + Impressionism LoRA + Lightning 4-step. POST /generate -> PNG.",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image prompt.")
    negative_prompt: str = Field(
        default_factory=lambda: _DEFAULT_NEGATIVE_PROMPT,
        description="Negative prompt.",
    )
    width: int = Field(default_factory=lambda: _DEFAULT_WIDTH, ge=512, le=1536)
    height: int = Field(default_factory=lambda: _DEFAULT_HEIGHT, ge=512, le=1536)
    steps: int = Field(default_factory=lambda: _DEFAULT_STEPS, ge=1, le=8)
    guidance_scale: float = Field(default_factory=lambda: _DEFAULT_GUIDANCE_SCALE, ge=0.0, le=5.0)
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


@app.post(
    "/generate",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}, "description": "Generated PNG image"}},
)
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

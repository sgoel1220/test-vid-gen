"""ComfyUI-Bare Horror-Painting Server — POST a prompt, get a PNG back.

Stack:
  - Base:      AlbedoBase XL v3.1 (loaded from safetensors checkpoint)
  - LoRAs (fused in order):
      detail_tweaker_xl        strength 1.0
      xl_more_art_full         strength 0.7
      midjourney_mimic         strength 0.6
      impressionism_sdxl       strength 0.4
      andreas_achenbach_sdxl   strength 0.4
  - Scheduler: DPMSolverMultistepScheduler (dpmpp_2m / karras)
  - 34 steps, CFG 2.0, 1216×832

~8-10 GB VRAM peak.
"""

from __future__ import annotations

import gc
import io
import logging
import os
import random
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import torch  # type: ignore[import-not-found]
from diffusers import (  # type: ignore[import-not-found]
    DPMSolverMultistepScheduler,
    StableDiffusionXLPipeline,
)
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CHECKPOINT_PATH = os.getenv(
    "CHECKPOINT_PATH", "/models/checkpoints/albedobase_xl_v31.safetensors"
)
_LORAS_DIR = os.getenv("LORAS_DIR", "/models/loras")
_DEFAULT_STEPS = int(os.getenv("DEFAULT_STEPS", "34"))
_DEFAULT_CFG = float(os.getenv("DEFAULT_CFG", "2.0"))
_DEFAULT_WIDTH = int(os.getenv("DEFAULT_WIDTH", "1216"))
_DEFAULT_HEIGHT = int(os.getenv("DEFAULT_HEIGHT", "832"))
_DEFAULT_NEGATIVE_PROMPT = (
    "deformed, ng_deepnegative_v1_75t, (deformed, distorted, disfigured:1.5), "
    "(mutated hands and fingers:1.5), monochrome background, furry, loli, poorly drawn, "
    "bad anatomy, wrong anatomy, extra limbs, missing limb, floating limbs, missing fingers, "
    "elongated hands, disconnected limbs, mutation, mutated, ugly, disgusting, blurry, "
    "blurry eyes, background characters, muscular, smooth, clean, minimalist, sleek, modern, "
    "photorealistic, sharp details, hyperdetailed, fine details, smooth rendering, digital art"
)

# Maximum pixel budget to prevent CUDA OOM on oversized requests (~1216×832 = ~1M px)
_MAX_PIXEL_BUDGET = int(os.getenv("MAX_PIXEL_BUDGET", str(1216 * 832 * 2)))  # 2× headroom

# LoRA definitions — ORDER AND WEIGHTS ARE CRITICAL (must match workflow_api.json exactly)
# Each entry: (adapter_name, filename, strength)
_LORAS: list[tuple[str, str, float]] = [
    ("dt",  "detail_tweaker_xl.safetensors",      1.0),
    ("art", "xl_more_art_full.safetensors",        0.7),
    ("mj",  "midjourney_mimic.safetensors",        0.6),
    ("imp", "impressionism_sdxl.safetensors",      0.4),
    ("aa",  "andreas_achenbach_sdxl.safetensors",  0.4),
]

_pipe: StableDiffusionXLPipeline | None = None
_ready = False
_load_error: str | None = None

# Serialize GPU access — prevents concurrent requests from racing on the shared pipeline
_gpu_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load() -> StableDiffusionXLPipeline:
    global _pipe, _ready, _load_error
    if _pipe is not None:
        return _pipe

    t0 = time.perf_counter()

    try:
        logger.info("[1/4] Loading AlbedoBase XL v3.1 from %s ...", _CHECKPOINT_PATH)
        _pipe = StableDiffusionXLPipeline.from_single_file(
            _CHECKPOINT_PATH,
            torch_dtype=torch.float16,
        ).to("cuda")
        logger.info(
            "[1/4] Checkpoint loaded. VRAM: %.2f GB (%.1fs)",
            torch.cuda.memory_allocated() / 1024**3,
            time.perf_counter() - t0,
        )

        logger.info("[2/4] Loading %d LoRAs ...", len(_LORAS))
        for adapter_name, filename, _ in _LORAS:
            path = os.path.join(_LORAS_DIR, filename)
            logger.info("  Loading LoRA: %s (adapter=%s)", filename, adapter_name)
            _pipe.load_lora_weights(path, adapter_name=adapter_name)

        adapter_names = [a for a, _, _ in _LORAS]
        adapter_weights = [w for _, _, w in _LORAS]
        logger.info("[3/4] Fusing LoRAs: %s weights=%s ...", adapter_names, adapter_weights)
        _pipe.set_adapters(adapter_names, adapter_weights=adapter_weights)
        _pipe.fuse_lora()
        _pipe.unload_lora_weights()
        logger.info(
            "[3/4] LoRAs fused. VRAM: %.2f GB",
            torch.cuda.memory_allocated() / 1024**3,
        )

        logger.info("[4/4] Setting scheduler (dpmpp_2m / karras) ...")
        _pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            _pipe.scheduler.config,
            use_karras_sigmas=True,
            algorithm_type="dpmsolver++",
            solver_order=2,
        )
        _pipe.set_progress_bar_config(disable=True)
        torch.cuda.empty_cache()
        gc.collect()

        elapsed = time.perf_counter() - t0
        logger.info(
            "Pipeline ready. VRAM: %.2f GB. Total startup: %.1fs",
            torch.cuda.memory_allocated() / 1024**3,
            elapsed,
        )
        logger.info("Application startup complete.")
        _ready = True

    except Exception as exc:
        # Reset partial state so /health does not report model_loaded=True
        _pipe = None
        _ready = False
        _load_error = str(exc)
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
        logger.exception("Model loading failed — server will start but /health returns 503")
    yield


app = FastAPI(
    title="ComfyUI Bare Horror-Painting Server",
    description="AlbedoBase XL v3.1 + 5 LoRAs (fused). POST /generate → PNG.",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    vram_gb: float | None = None
    load_error: str | None = None


class ReadyResponse(BaseModel):
    status: str


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    negative_prompt: str = Field(default=_DEFAULT_NEGATIVE_PROMPT)
    width: int = Field(default=_DEFAULT_WIDTH, ge=512, le=2048)
    height: int = Field(default=_DEFAULT_HEIGHT, ge=512, le=2048)
    steps: int = Field(default=_DEFAULT_STEPS, ge=1, le=100)
    cfg: float = Field(default=_DEFAULT_CFG, ge=0.0, le=20.0)
    seed: int | None = Field(None, ge=0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health(response: Response) -> HealthResponse:
    vram: float | None = None
    if torch.cuda.is_available():
        vram = round(torch.cuda.memory_allocated() / 1024**3, 2)
    if not _ready:
        response.status_code = 503
        return HealthResponse(
            status="starting" if _load_error is None else "error",
            model_loaded=False,
            vram_gb=vram,
            load_error=_load_error,
        )
    return HealthResponse(status="ok", model_loaded=True, vram_gb=vram)


@app.get("/ready")
def ready() -> ReadyResponse:
    if _ready:
        return ReadyResponse(status="ready")
    raise HTTPException(status_code=503, detail="Model not ready")


@app.post(
    "/generate",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def generate(request: GenerateRequest) -> Response:
    if not _ready:
        raise HTTPException(status_code=503, detail="Model not ready")

    # Reject requests that exceed the validated VRAM envelope
    if request.width * request.height > _MAX_PIXEL_BUDGET:
        raise HTTPException(
            status_code=422,
            detail=(
                f"width×height ({request.width}×{request.height} = "
                f"{request.width * request.height:,} px) exceeds the maximum pixel budget "
                f"({_MAX_PIXEL_BUDGET:,} px). Reduce resolution."
            ),
        )

    # Enforce single-flight GPU access — return 503 if another request is running
    if not _gpu_lock.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="GPU busy — try again shortly")

    try:
        pipe = _pipe
        assert pipe is not None  # guarded by _ready check above

        seed = request.seed if request.seed is not None else random.randint(0, 2**53)
        logger.info(
            "Generating: steps=%d size=%dx%d cfg=%.1f seed=%d",
            request.steps,
            request.width,
            request.height,
            request.cfg,
            seed,
        )
        t0 = time.perf_counter()

        generator = torch.Generator(device="cuda").manual_seed(seed)
        try:
            result = pipe(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                width=request.width,
                height=request.height,
                num_inference_steps=request.steps,
                guidance_scale=request.cfg,
                generator=generator,
            )
        except RuntimeError as exc:
            torch.cuda.empty_cache()
            gc.collect()
            if "out of memory" in str(exc).lower():
                logger.error("CUDA OOM during generation: %s", exc)
                raise HTTPException(status_code=507, detail="GPU out of memory") from exc
            raise

        image = result.images[0]

        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        elapsed = time.perf_counter() - t0
        logger.info("Done in %.1fs", elapsed)
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={"X-Seed": str(seed), "X-Elapsed": f"{elapsed:.2f}"},
        )
    finally:
        torch.cuda.empty_cache()
        _gpu_lock.release()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8006, reload=False)

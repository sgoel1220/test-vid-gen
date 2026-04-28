"""SDXL image generation server for RunPod — base model only."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

import torch
from diffusers import DPMSolverMultistepScheduler, StableDiffusionXLPipeline
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.float16

# Local .safetensors path (set by download.py from CivitAI)
BASE_MODEL_PATH = os.getenv("BASE_MODEL_PATH")
# HuggingFace fallback (only used if BASE_MODEL_PATH is not set)
BASE_MODEL_ID = os.getenv("BASE_MODEL_ID", "stabilityai/stable-diffusion-xl-base-1.0")
HF_TOKEN = os.getenv("HF_TOKEN")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

base_pipe: Optional[StableDiffusionXLPipeline] = None
_gpu_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 1344
    height: int = 768
    steps: int = 35
    cfg: float = 4.5
    seed: Optional[int] = None
    clip_skip: int = 1

    @field_validator("width", "height")
    @classmethod
    def must_be_divisible_by_8(cls, v: int) -> int:
        if v % 8 != 0:
            raise ValueError("width/height must be divisible by 8")
        return v

    @field_validator("steps")
    @classmethod
    def steps_range(cls, v: int) -> int:
        if not (1 <= v <= 150):
            raise ValueError("steps must be between 1 and 150")
        return v


class GenerateResponse(BaseModel):
    image_b64: str
    seed: int
    elapsed_seconds: float
    width: int
    height: int
    steps: int
    cfg: float
    clip_skip: int


class HealthResponse(BaseModel):
    status: str
    device: str
    base_model: str
    base_loaded: bool
    cuda_memory_allocated_gb: Optional[float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler() -> DPMSolverMultistepScheduler:
    return DPMSolverMultistepScheduler(
        use_karras_sigmas=True,
        algorithm_type="sde-dpmsolver++",
    )


def _enable_memory_opts(pipe: StableDiffusionXLPipeline) -> None:
    if DEVICE != "cuda":
        return
    try:
        pipe.enable_xformers_memory_efficient_attention()
        log.info("xformers enabled")
    except Exception as exc:
        log.warning("xformers not available: %s", exc)
    pipe.enable_vae_slicing()
    pipe.enable_vae_tiling()


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _load_models() -> None:
    global base_pipe

    with torch.inference_mode():
        if BASE_MODEL_PATH:
            log.info("Loading base from local file: %s", BASE_MODEL_PATH)
            base_pipe = StableDiffusionXLPipeline.from_single_file(
                BASE_MODEL_PATH,
                torch_dtype=DTYPE,
                use_safetensors=True,
            )
        else:
            log.info("Loading base from HuggingFace: %s", BASE_MODEL_ID)
            base_pipe = StableDiffusionXLPipeline.from_pretrained(
                BASE_MODEL_ID,
                torch_dtype=DTYPE,
                use_safetensors=True,
                variant="fp16",
                token=HF_TOKEN,
            )

    base_pipe.scheduler = _make_scheduler()
    base_pipe = base_pipe.to(DEVICE)
    _enable_memory_opts(base_pipe)
    base_pipe.set_progress_bar_config(disable=True)

    if DEVICE == "cuda":
        torch.cuda.empty_cache()
        vram_gb = torch.cuda.memory_allocated() / 1024**3
        log.info("Base model loaded. VRAM used: %.2f GB", vram_gb)
    else:
        log.info("Base model loaded.")


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _generate_sync(req: GenerateRequest) -> GenerateResponse:
    assert base_pipe is not None

    t0 = time.perf_counter()
    seed = req.seed if req.seed is not None else int(torch.randint(0, 2**32, (1,)).item())

    log.info(
        "Generating seed=%d %dx%d steps=%d cfg=%.1f clip_skip=%d",
        seed, req.width, req.height, req.steps, req.cfg, req.clip_skip,
    )

    generator = torch.Generator(device=DEVICE).manual_seed(seed)

    with torch.inference_mode():
        result = base_pipe(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt or None,
            width=req.width,
            height=req.height,
            num_inference_steps=req.steps,
            guidance_scale=req.cfg,
            clip_skip=req.clip_skip,
            generator=generator,
        )
    image: Image.Image = result.images[0]

    if DEVICE == "cuda":
        torch.cuda.empty_cache()

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    elapsed = time.perf_counter() - t0
    log.info("Done in %.1fs", elapsed)

    return GenerateResponse(
        image_b64=b64,
        seed=seed,
        elapsed_seconds=round(elapsed, 2),
        width=image.width,
        height=image.height,
        steps=req.steps,
        cfg=req.cfg,
        clip_skip=req.clip_skip,
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _load_models()
    log.info("Server ready.")
    yield


app = FastAPI(title="SDXL RunPod Server", version="2.0.0", lifespan=_lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    cuda_mem: Optional[float] = None
    if torch.cuda.is_available():
        cuda_mem = round(torch.cuda.memory_allocated() / 1024**3, 2)
    return HealthResponse(
        status="ok",
        device=DEVICE,
        base_model=BASE_MODEL_PATH or BASE_MODEL_ID,
        base_loaded=base_pipe is not None,
        cuda_memory_allocated_gb=cuda_mem,
    )


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    if base_pipe is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    async with _gpu_lock:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _generate_sync, req)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            log.error("CUDA OOM for %dx%d steps=%d — cleared cache", req.width, req.height, req.steps)
            raise HTTPException(status_code=503, detail="GPU out of memory — try smaller resolution or fewer steps")
    return result


@app.post(
    "/generate/preview",
    responses={200: {"content": {"image/png": {}}}},
    response_class=Response,
    summary="Generate and preview as PNG",
)
async def generate_preview(req: GenerateRequest) -> Response:
    """Same as /generate but returns the raw PNG — renders inline in Swagger UI."""
    if base_pipe is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")
    async with _gpu_lock:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _generate_sync, req)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            log.error("CUDA OOM for %dx%d steps=%d — cleared cache", req.width, req.height, req.steps)
            raise HTTPException(status_code=503, detail="GPU out of memory — try smaller resolution or fewer steps")
    png_bytes = base64.b64decode(result.image_b64)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Seed": str(result.seed),
            "X-Elapsed": str(result.elapsed_seconds),
        },
    )

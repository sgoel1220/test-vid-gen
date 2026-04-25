"""SDXL image generation server for RunPod."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

import torch
from diffusers import (
    DPMSolverMultistepScheduler,
    StableDiffusionXLImg2ImgPipeline,
    StableDiffusionXLPipeline,
)
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LORAS_DIR = Path(os.getenv("LORAS_DIR", "/loras"))
HF_TOKEN = os.getenv("HF_TOKEN")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.float16

# Set BASE_MODEL_PATH to a local .safetensors to load Juggernaut XL (or any
# single-file checkpoint) instead of downloading from HuggingFace.
BASE_MODEL_PATH = os.getenv("BASE_MODEL_PATH")
BASE_MODEL_ID = os.getenv("BASE_MODEL_ID", "stabilityai/stable-diffusion-xl-base-1.0")
REFINER_MODEL_ID = "stabilityai/stable-diffusion-xl-refiner-1.0"

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

base_pipe: Optional[StableDiffusionXLPipeline] = None
refiner_pipe: Optional[StableDiffusionXLImg2ImgPipeline] = None
hires_pipe: Optional[StableDiffusionXLImg2ImgPipeline] = None  # shares base weights

lora_registry: dict[str, Path] = {}
loaded_lora_set: frozenset[str] = frozenset()

_gpu_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class LoraSpec(BaseModel):
    name: str
    weight: float = 1.0


class GenerateRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    loras: list[LoraSpec] = Field(default_factory=list)
    width: int = 1344
    height: int = 768
    steps: int = 35
    cfg: float = 4.5
    seed: Optional[int] = None
    clip_skip: int = 1
    # Refiner pass
    refiner_denoise: float = 0.35
    # HiRes fix: generates at width/hires_scale × height/hires_scale,
    # upscales with Lanczos, then runs an img2img pass at the target res.
    hires_enabled: bool = False
    hires_scale: float = 2.0
    hires_steps: int = 15
    hires_denoise: float = 0.3

    @field_validator("width", "height")
    @classmethod
    def must_be_divisible_by_8(cls, v: int) -> int:
        if v % 8 != 0:
            raise ValueError("width/height must be divisible by 8")
        return v

    @field_validator("steps", "hires_steps")
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
    loras_applied: list[str]
    hires_enabled: bool


class LoraInfo(BaseModel):
    name: str
    path: str


class HealthResponse(BaseModel):
    status: str
    device: str
    base_model: str
    base_loaded: bool
    refiner_loaded: bool
    loras_available: int
    cuda_memory_allocated_gb: Optional[float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler() -> DPMSolverMultistepScheduler:
    """DPM++ 2M SDE with Karras sigmas — matches the recommended stack."""
    return DPMSolverMultistepScheduler(
        use_karras_sigmas=True,
        algorithm_type="sde-dpmsolver++",
    )


def _scan_loras() -> dict[str, Path]:
    registry: dict[str, Path] = {}
    if not LORAS_DIR.exists():
        log.warning("LoRA directory %s does not exist — skipping scan", LORAS_DIR)
        return registry
    for path in sorted(LORAS_DIR.glob("**/*.safetensors")):
        name = path.stem
        if name in registry:
            log.warning("Duplicate LoRA '%s' (%s vs %s) — keeping first", name, registry[name], path)
        else:
            registry[name] = path
            log.info("Registered LoRA: %s -> %s", name, path)
    log.info("LoRA scan complete: %d adapters found", len(registry))
    return registry


def _enable_memory_opts(pipe: StableDiffusionXLPipeline | StableDiffusionXLImg2ImgPipeline) -> None:
    if DEVICE != "cuda":
        return
    try:
        pipe.enable_xformers_memory_efficient_attention()
        log.info("xformers enabled on %s", type(pipe).__name__)
    except Exception as exc:
        log.warning("xformers not available: %s", exc)
    pipe.enable_vae_slicing()


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _load_models() -> None:
    global base_pipe, refiner_pipe, hires_pipe, lora_registry, loaded_lora_set

    # ── Base model ──────────────────────────────────────────────────────────
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
    log.info("Base model loaded.")

    # ── HiRes img2img (reuses base model weights, no extra VRAM) ────────────
    hires_pipe = StableDiffusionXLImg2ImgPipeline(
        vae=base_pipe.vae,
        text_encoder=base_pipe.text_encoder,
        text_encoder_2=base_pipe.text_encoder_2,
        tokenizer=base_pipe.tokenizer,
        tokenizer_2=base_pipe.tokenizer_2,
        unet=base_pipe.unet,
        scheduler=_make_scheduler(),
    )
    hires_pipe = hires_pipe.to(DEVICE)
    hires_pipe.set_progress_bar_config(disable=True)
    log.info("HiRes pipe ready (shared base weights).")

    # ── Refiner ─────────────────────────────────────────────────────────────
    log.info("Loading refiner: %s", REFINER_MODEL_ID)
    refiner_pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
        REFINER_MODEL_ID,
        torch_dtype=DTYPE,
        use_safetensors=True,
        variant="fp16",
        token=HF_TOKEN,
    )
    if DEVICE == "cuda":
        # Keep refiner in RAM; stream layers to GPU on demand — saves ~8 GB VRAM.
        refiner_pipe.enable_model_cpu_offload()
    else:
        refiner_pipe = refiner_pipe.to(DEVICE)
    _enable_memory_opts(refiner_pipe)
    refiner_pipe.set_progress_bar_config(disable=True)
    log.info("Refiner loaded.")

    lora_registry = _scan_loras()


# ---------------------------------------------------------------------------
# LoRA hot-swap
# ---------------------------------------------------------------------------

def _apply_loras(lora_specs: list[LoraSpec]) -> list[str]:
    global loaded_lora_set
    assert base_pipe is not None

    requested_names = {spec.name for spec in lora_specs}

    for spec in lora_specs:
        if spec.name not in lora_registry:
            raise HTTPException(
                status_code=422,
                detail=f"LoRA '{spec.name}' not found. Available: {sorted(lora_registry.keys())}",
            )

    if loaded_lora_set != frozenset(requested_names):
        if loaded_lora_set:
            try:
                base_pipe.unload_lora_weights()
                log.info("Unloaded LoRAs: %s", sorted(loaded_lora_set))
            except Exception as exc:
                log.warning("Failed to unload LoRAs: %s", exc)
        loaded_lora_set = frozenset()

    if not lora_specs:
        return []

    adapter_names: list[str] = []
    adapter_weights: list[float] = []
    for spec in lora_specs:
        path = lora_registry[spec.name]
        try:
            base_pipe.load_lora_weights(
                str(path.parent),
                weight_name=path.name,
                adapter_name=spec.name,
            )
            adapter_names.append(spec.name)
            adapter_weights.append(spec.weight)
            log.info("Loaded LoRA '%s' weight=%.2f", spec.name, spec.weight)
        except Exception as exc:
            raise HTTPException(500, f"Failed to load LoRA '{spec.name}': {exc}") from exc

    base_pipe.set_adapters(adapter_names, adapter_weights=adapter_weights)
    loaded_lora_set = frozenset(requested_names)
    return adapter_names


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _round8(v: float) -> int:
    return max(8, int(v) // 8 * 8)


def _generate_sync(req: GenerateRequest) -> GenerateResponse:
    assert base_pipe is not None
    assert refiner_pipe is not None
    assert hires_pipe is not None

    t0 = time.perf_counter()
    seed = req.seed if req.seed is not None else int(torch.randint(0, 2**32, (1,)).item())

    loras_applied = _apply_loras(req.loras)

    # When HiRes is enabled, generate at half res first
    gen_w = _round8(req.width / req.hires_scale) if req.hires_enabled else req.width
    gen_h = _round8(req.height / req.hires_scale) if req.hires_enabled else req.height

    log.info(
        "Generating seed=%d %dx%d steps=%d cfg=%.1f clip_skip=%d hires=%s loras=%s",
        seed, gen_w, gen_h, req.steps, req.cfg, req.clip_skip, req.hires_enabled, loras_applied,
    )

    generator = torch.Generator(device=DEVICE).manual_seed(seed)

    # ── Base pass ────────────────────────────────────────────────────────────
    base_output = base_pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or None,
        width=gen_w,
        height=gen_h,
        num_inference_steps=req.steps,
        guidance_scale=req.cfg,
        clip_skip=req.clip_skip,
        generator=generator,
        output_type="latent",
        denoising_end=1.0 - req.refiner_denoise,
    )
    latents = base_output.images  # type: ignore[attr-defined]

    if DEVICE == "cuda":
        torch.cuda.empty_cache()

    # ── Refiner pass ─────────────────────────────────────────────────────────
    refiner_generator = torch.Generator(device=DEVICE).manual_seed(seed)
    refined = refiner_pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or None,
        image=latents,
        num_inference_steps=req.steps,
        denoising_start=1.0 - req.refiner_denoise,
        guidance_scale=req.cfg,
        generator=refiner_generator,
    )
    image: Image.Image = refined.images[0]

    if DEVICE == "cuda":
        torch.cuda.empty_cache()

    # ── HiRes fix ────────────────────────────────────────────────────────────
    if req.hires_enabled:
        log.info("HiRes upscale %dx%d → %dx%d, denoise=%.2f steps=%d",
                 gen_w, gen_h, req.width, req.height, req.hires_denoise, req.hires_steps)
        image = image.resize((req.width, req.height), Image.LANCZOS)
        hires_generator = torch.Generator(device=DEVICE).manual_seed(seed)
        hires_out = hires_pipe(
            prompt=req.prompt,
            negative_prompt=req.negative_prompt or None,
            image=image,
            num_inference_steps=req.hires_steps,
            strength=req.hires_denoise,
            guidance_scale=req.cfg,
            clip_skip=req.clip_skip,
            generator=hires_generator,
        )
        image = hires_out.images[0]

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
        loras_applied=loras_applied,
        hires_enabled=req.hires_enabled,
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="SDXL RunPod Server", version="1.0.0")


@app.on_event("startup")
def startup_event() -> None:
    _load_models()
    log.info("Server ready.")


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
        refiner_loaded=refiner_pipe is not None,
        loras_available=len(lora_registry),
        cuda_memory_allocated_gb=cuda_mem,
    )


@app.get("/loras", response_model=list[LoraInfo])
def list_loras() -> list[LoraInfo]:
    return [LoraInfo(name=n, path=str(p)) for n, p in sorted(lora_registry.items())]


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    if base_pipe is None or refiner_pipe is None:
        raise HTTPException(status_code=503, detail="Models not loaded yet")
    async with _gpu_lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _generate_sync, req)
    return result


@app.post(
    "/generate/preview",
    responses={200: {"content": {"image/png": {}}}},
    response_class=Response,
    summary="Generate and preview as PNG",
)
async def generate_preview(req: GenerateRequest) -> Response:
    """Same as /generate but returns the raw PNG image — renders inline in Swagger UI."""
    if base_pipe is None or refiner_pipe is None:
        raise HTTPException(status_code=503, detail="Models not loaded yet")
    async with _gpu_lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _generate_sync, req)
    png_bytes = base64.b64decode(result.image_b64)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Seed": str(result.seed),
            "X-Elapsed": str(result.elapsed_seconds),
            "X-Steps": str(result.steps),
        },
    )


@app.post("/reload-loras")
def reload_loras() -> JSONResponse:
    global lora_registry, loaded_lora_set
    lora_registry = _scan_loras()
    loaded_lora_set = frozenset()
    if base_pipe is not None:
        try:
            base_pipe.unload_lora_weights()
        except Exception:
            pass
    return JSONResponse({"registered": sorted(lora_registry.keys())})

"""SDXL image generation server for RunPod."""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

import torch
from diffusers import StableDiffusionXLPipeline, StableDiffusionXLImg2ImgPipeline
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
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

BASE_MODEL_ID = "stabilityai/stable-diffusion-xl-base-1.0"
REFINER_MODEL_ID = "stabilityai/stable-diffusion-xl-refiner-1.0"

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

base_pipe: Optional[StableDiffusionXLPipeline] = None
refiner_pipe: Optional[StableDiffusionXLImg2ImgPipeline] = None

# Registry: name -> path on disk
lora_registry: dict[str, Path] = {}

# Which LoRA adapters are currently loaded into the base pipe
loaded_lora_set: frozenset[str] = frozenset()

# Serialize GPU work so requests don't race
_gpu_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Models
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
    steps: int = 30
    cfg: float = 6.0
    seed: Optional[int] = None
    refiner_denoise: float = 0.35

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
    loras_applied: list[str]


class LoraInfo(BaseModel):
    name: str
    path: str


class HealthResponse(BaseModel):
    status: str
    device: str
    base_loaded: bool
    refiner_loaded: bool
    loras_available: int
    cuda_memory_allocated_gb: Optional[float]


# ---------------------------------------------------------------------------
# Startup / model loading
# ---------------------------------------------------------------------------

def _scan_loras() -> dict[str, Path]:
    registry: dict[str, Path] = {}
    if not LORAS_DIR.exists():
        log.warning("LoRA directory %s does not exist — skipping scan", LORAS_DIR)
        return registry
    for path in sorted(LORAS_DIR.glob("**/*.safetensors")):
        name = path.stem
        if name in registry:
            log.warning("Duplicate LoRA name '%s' (%s vs %s) — using first", name, registry[name], path)
        else:
            registry[name] = path
            log.info("Registered LoRA: %s -> %s", name, path)
    log.info("LoRA scan complete: %d adapters found", len(registry))
    return registry


def _load_models() -> None:
    global base_pipe, refiner_pipe, lora_registry, loaded_lora_set

    log.info("Loading SDXL base from %s …", BASE_MODEL_ID)
    base_pipe = StableDiffusionXLPipeline.from_pretrained(
        BASE_MODEL_ID,
        torch_dtype=DTYPE,
        use_safetensors=True,
        variant="fp16",
        token=HF_TOKEN,
    )
    base_pipe = base_pipe.to(DEVICE)
    if DEVICE == "cuda":
        try:
            base_pipe.enable_xformers_memory_efficient_attention()
            log.info("xformers enabled for base pipe")
        except Exception as exc:
            log.warning("xformers not available for base pipe: %s", exc)
    base_pipe.set_progress_bar_config(disable=True)
    log.info("Base model loaded.")

    log.info("Loading SDXL refiner from %s …", REFINER_MODEL_ID)
    refiner_pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
        REFINER_MODEL_ID,
        torch_dtype=DTYPE,
        use_safetensors=True,
        variant="fp16",
        token=HF_TOKEN,
    )
    refiner_pipe = refiner_pipe.to(DEVICE)
    if DEVICE == "cuda":
        try:
            refiner_pipe.enable_xformers_memory_efficient_attention()
            log.info("xformers enabled for refiner pipe")
        except Exception as exc:
            log.warning("xformers not available for refiner pipe: %s", exc)
    refiner_pipe.set_progress_bar_config(disable=True)
    log.info("Refiner model loaded.")

    lora_registry = _scan_loras()


# ---------------------------------------------------------------------------
# LoRA hot-swap helpers
# ---------------------------------------------------------------------------

def _apply_loras(lora_specs: list[LoraSpec]) -> list[str]:
    """Load and fuse LoRA adapters into the base pipe; unload previous ones."""
    global loaded_lora_set

    assert base_pipe is not None

    requested_names = {spec.name for spec in lora_specs}

    # Validate all requested LoRAs exist
    for spec in lora_specs:
        if spec.name not in lora_registry:
            raise HTTPException(
                status_code=422,
                detail=f"LoRA '{spec.name}' not found. Available: {sorted(lora_registry.keys())}",
            )

    # Unload previous adapters if the set changed
    if loaded_lora_set != frozenset(requested_names):
        if loaded_lora_set:
            try:
                base_pipe.unload_lora_weights()
                log.info("Unloaded previous LoRAs: %s", sorted(loaded_lora_set))
            except Exception as exc:
                log.warning("Failed to unload LoRAs: %s", exc)
        loaded_lora_set = frozenset()

    if not lora_specs:
        return []

    # Load all adapters (diffusers supports multi-adapter loading)
    adapter_names: list[str] = []
    adapter_weights: list[float] = []
    for spec in lora_specs:
        lora_path = lora_registry[spec.name]
        adapter_name = spec.name
        try:
            base_pipe.load_lora_weights(
                str(lora_path.parent),
                weight_name=lora_path.name,
                adapter_name=adapter_name,
            )
            adapter_names.append(adapter_name)
            adapter_weights.append(spec.weight)
            log.info("Loaded LoRA adapter '%s' weight=%.2f", adapter_name, spec.weight)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to load LoRA '{spec.name}': {exc}",
            ) from exc

    if adapter_names:
        base_pipe.set_adapters(adapter_names, adapter_weights=adapter_weights)

    loaded_lora_set = frozenset(requested_names)
    return adapter_names


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _generate_sync(req: GenerateRequest) -> GenerateResponse:
    assert base_pipe is not None
    assert refiner_pipe is not None

    t0 = time.perf_counter()

    seed = req.seed if req.seed is not None else int(torch.randint(0, 2**32, (1,)).item())
    generator = torch.Generator(device=DEVICE).manual_seed(seed)

    loras_applied = _apply_loras(req.loras)

    log.info(
        "Generating seed=%d %dx%d steps=%d cfg=%.1f loras=%s",
        seed, req.width, req.height, req.steps, req.cfg, loras_applied,
    )

    # Base pass — produce latents for the refiner
    base_output = base_pipe(
        prompt=req.prompt,
        negative_prompt=req.negative_prompt or None,
        width=req.width,
        height=req.height,
        num_inference_steps=req.steps,
        guidance_scale=req.cfg,
        generator=generator,
        output_type="latent",
        denoising_end=1.0 - req.refiner_denoise,
    )
    latents = base_output.images  # type: ignore[attr-defined]

    # Refiner pass
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
    image = refined.images[0]

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    elapsed = time.perf_counter() - t0
    log.info("Generation complete in %.1fs", elapsed)

    return GenerateResponse(
        image_b64=b64,
        seed=seed,
        elapsed_seconds=round(elapsed, 2),
        width=req.width,
        height=req.height,
        steps=req.steps,
        cfg=req.cfg,
        loras_applied=loras_applied,
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


@app.post("/reload-loras")
def reload_loras() -> JSONResponse:
    """Rescan /loras directory and update registry (no model reload needed)."""
    global lora_registry, loaded_lora_set
    lora_registry = _scan_loras()
    loaded_lora_set = frozenset()  # Force re-apply on next request
    if base_pipe is not None:
        try:
            base_pipe.unload_lora_weights()
        except Exception:
            pass
    return JSONResponse({"registered": sorted(lora_registry.keys())})

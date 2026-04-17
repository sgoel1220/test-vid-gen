"""SDXL-Lightning image server — POST a prompt, get a PNG back.

4-step distilled SDXL. ~7 GB VRAM, fits RTX A4000 (16 GB) with headroom.
No quantization needed. No HF token needed (ungated).
"""

from __future__ import annotations

import io
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import torch
from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field
from safetensors.torch import load_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model singleton
# ---------------------------------------------------------------------------

_BASE_MODEL = "stabilityai/stable-diffusion-xl-base-1.0"
_LIGHTNING_REPO = "ByteDance/SDXL-Lightning"
_LIGHTNING_CKPT = "sdxl_lightning_4step_unet.safetensors"

_pipe: StableDiffusionXLPipeline | None = None


def _load() -> StableDiffusionXLPipeline:
    global _pipe
    if _pipe is None:
        logger.info("Loading SDXL-Lightning (4-step UNet) …")

        unet_config = UNet2DConditionModel.load_config(_BASE_MODEL, subfolder="unet")
        unet = UNet2DConditionModel.from_config(unet_config).to(
            device="cuda", dtype=torch.float16,
        )

        unet.load_state_dict(
            load_file(
                hf_hub_download(_LIGHTNING_REPO, _LIGHTNING_CKPT),
                device="cuda",
            ),
        )

        _pipe = StableDiffusionXLPipeline.from_pretrained(
            _BASE_MODEL,
            unet=unet,
            torch_dtype=torch.float16,
            variant="fp16",
        ).to("cuda")

        _pipe.scheduler = EulerDiscreteScheduler.from_config(
            _pipe.scheduler.config, timestep_spacing="trailing",
        )

        logger.info("SDXL-Lightning pipeline ready.")
    return _pipe


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _load()
    yield


app = FastAPI(
    title="Image Server",
    description="SDXL-Lightning generation API (4-step, ~7 GB VRAM).",
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image prompt.")
    width: int = Field(1024, ge=512, le=1536)
    height: int = Field(1024, ge=512, le=1536)
    steps: int = Field(4, ge=1, le=8)  # Lightning: 4 steps optimal
    seed: int | None = Field(None, ge=0)


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready")
def ready() -> ReadyResponse:
    if _pipe is not None:
        return ReadyResponse(status="ready")
    raise HTTPException(status_code=503, detail="Model loading")


@app.post("/generate", response_class=Response)
def generate(request: GenerateRequest) -> Response:
    """Generate an image and return it as PNG bytes."""
    pipe = _load()

    generator: torch.Generator | None = None
    if request.seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(request.seed)

    logger.info(
        "Generating image: steps=%d size=%dx%d seed=%s",
        request.steps,
        request.width,
        request.height,
        request.seed,
    )

    result = pipe(
        prompt=request.prompt,
        width=request.width,
        height=request.height,
        num_inference_steps=request.steps,
        guidance_scale=0.0,
        generator=generator,
    )

    img = result.images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return Response(content=buf.read(), media_type="image/png")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8006, reload=False)

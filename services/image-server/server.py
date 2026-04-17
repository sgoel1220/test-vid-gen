"""Minimal Z-Image-Turbo server — POST a prompt, get a PNG back (~1s inference)."""

from __future__ import annotations

import io
import logging
from typing import Optional

import torch
from diffusers import DiffusionPipeline
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model singleton
# ---------------------------------------------------------------------------

_MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"
_pipe: Optional[DiffusionPipeline] = None


def _load() -> DiffusionPipeline:
    global _pipe
    if _pipe is None:
        logger.info("Loading Z-Image-Turbo pipeline: %s …", _MODEL_ID)
        _pipe = DiffusionPipeline.from_pretrained(
            _MODEL_ID, torch_dtype=torch.bfloat16, trust_remote_code=True
        )
        _pipe = _pipe.to("cuda")
        logger.info("Z-Image-Turbo pipeline ready.")
    return _pipe


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Image Server", description="Z-Image-Turbo generation API (~1s inference).")


@app.on_event("startup")
def _startup() -> None:
    _load()


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Image prompt.")
    negative_prompt: str = Field("", description="Negative prompt.")
    width: int = Field(1024, ge=256, le=2048)
    height: int = Field(1024, ge=256, le=2048)
    steps: int = Field(8, ge=1, le=16)  # Turbo: 8-9 optimal
    guidance_scale: float = Field(0.0, ge=0.0, le=5.0)  # Turbo: 0.0 recommended
    seed: Optional[int] = Field(None, ge=0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate", response_class=Response)
def generate(request: GenerateRequest) -> Response:
    """Generate an image and return it as PNG bytes."""
    pipe = _load()

    generator = None
    if request.seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(request.seed)

    logger.info("Generating image: steps=%d guidance=%.1f seed=%s", request.steps, request.guidance_scale, request.seed)

    result = pipe(
        prompt=request.prompt,
        negative_prompt=request.negative_prompt or None,
        width=request.width,
        height=request.height,
        num_inference_steps=request.steps,
        guidance_scale=request.guidance_scale,
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

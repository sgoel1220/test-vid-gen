"""Minimal SFX server exposing /generate for creepy-brain orchestration.

Uses EzAudio (OpenSound/EzAudio) for text-to-sound-effects generation.
Model: s3_xl (flan-t5-xl text encoder + latent diffusion).
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING, Literal

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from api.ezaudio import EzAudio as EzAudioModel

WAV_MEDIA_TYPE = "audio/wav"
MODEL_NAME = "s3_xl"
SAMPLE_RATE = 24_000  # EzAudio always outputs 24 kHz

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Request body for POST /generate."""

    prompt: str = Field(..., min_length=1, max_length=500, description="Text description of the sound effect.")
    duration_sec: int = Field(5, ge=1, le=10, description="Duration in seconds (1–10).")
    seed: int = Field(0, ge=0, description="Random seed (0 = random).")
    guidance_scale: float = Field(5.0, ge=1.0, le=20.0, description="Classifier-free guidance scale.")
    guidance_rescale: float = Field(0.75, ge=0.0, le=1.0, description="Guidance rescale factor.")
    ddim_steps: int = Field(50, ge=1, le=200, description="Number of DDIM diffusion steps.")


class HealthResponse(BaseModel):
    """Response for GET /health (liveness)."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Response for GET /ready (model readiness)."""

    ready: bool = Field(..., description="True when EzAudio model is loaded.")


class GenerationResult(BaseModel):
    """Intermediate result from synchronous generation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    audio: np.ndarray = Field(description="Generated audio as 1-D float32 array.")
    sample_rate: int = Field(gt=0, description="Sample rate of the generated audio.")


# ---------------------------------------------------------------------------
# Model singleton (lazy-loaded)
# ---------------------------------------------------------------------------
_model: EzAudioModel | None = None
_model_lock = asyncio.Lock()
_model_error: str | None = None

# Single-slot GPU execution lock: prevents concurrent generation calls from
# racing shared GPU memory.
_gpu_busy = False


def _get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


async def _ensure_model_loaded() -> EzAudioModel:
    """Load EzAudio model if not already loaded."""
    global _model, _model_error
    async with _model_lock:
        if _model is not None:
            return _model
        if _model_error is not None:
            raise RuntimeError(f"Model failed to load: {_model_error}")

        logger.info("Loading EzAudio %s model...", MODEL_NAME)
        device = _get_device()
        logger.info("Using device: %s", device)

        from api.ezaudio import EzAudio as EzAudioModel  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        try:
            _model = await loop.run_in_executor(
                None,
                lambda: EzAudioModel(model_name=MODEL_NAME, device=device),
            )
        except Exception as exc:
            _model_error = str(exc)
            logger.exception("Failed to load EzAudio model")
            raise
        logger.info("EzAudio model loaded successfully")
        return _model


def _generate_sync(
    model: EzAudioModel,
    prompt: str,
    duration_sec: int,
    seed: int,
    guidance_scale: float,
    guidance_rescale: float,
    ddim_steps: int,
) -> GenerationResult:
    """Synchronous audio generation. Must run in a thread pool."""
    randomize_seed = seed == 0
    resolved_seed: int | None = None if randomize_seed else seed

    sr, audio = model.generate_audio(
        prompt,
        length=duration_sec,
        guidance_scale=guidance_scale,
        guidance_rescale=guidance_rescale,
        ddim_steps=ddim_steps,
        eta=1,
        random_seed=resolved_seed,
        randomize_seed=randomize_seed,
    )
    return GenerationResult(audio=audio, sample_rate=int(sr))


def _encode_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """Encode a 1-D float32 NumPy array to PCM-16 WAV bytes."""
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, audio_int16, sample_rate, format="wav", subtype="PCM_16")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="SFX Server",
    description="Text-to-sound-effects generation via EzAudio for creepy-brain orchestration.",
    version="1.0.0",
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — always returns 200 ok."""
    return HealthResponse()


@app.get("/ready", response_model=ReadinessResponse)
async def ready() -> ReadinessResponse:
    """Readiness probe — returns 503 until model is loaded or if load failed."""
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    if _model_error is not None:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"ready": False, "error": _model_error},
        )
    if _model is None:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"ready": False},
        )
    return ReadinessResponse(ready=True)


@app.post("/generate")
async def generate(request: GenerateRequest) -> Response:
    """Generate a WAV sound effect from a text prompt."""
    global _gpu_busy

    # Check and set before the first await — safe in asyncio's single-threaded
    # event loop: no other coroutine can run between these two statements.
    if _gpu_busy:
        raise HTTPException(status_code=503, detail="GPU busy — try again shortly.")
    _gpu_busy = True

    try:
        model = await _ensure_model_loaded()
    except Exception as exc:
        _gpu_busy = False
        raise HTTPException(status_code=503, detail=f"Model unavailable: {exc}") from exc

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _generate_sync(
                model=model,
                prompt=request.prompt,
                duration_sec=request.duration_sec,
                seed=request.seed,
                guidance_scale=request.guidance_scale,
                guidance_rescale=request.guidance_rescale,
                ddim_steps=request.ddim_steps,
            ),
        )
    except Exception as exc:
        logger.exception("Generation failed for prompt: %r", request.prompt)
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}") from exc
    finally:
        _gpu_busy = False

    wav_bytes = _encode_wav(result.audio, result.sample_rate)
    return Response(content=wav_bytes, media_type=WAV_MEDIA_TYPE)


@app.on_event("startup")
async def startup_event() -> None:
    """Kick off model loading in the background at startup."""
    logger.info("Starting SFX server (EzAudio %s)...", MODEL_NAME)
    asyncio.create_task(_ensure_model_loaded())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8008,
        log_level="info",
    )

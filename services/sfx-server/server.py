"""Minimal SFX-generation server exposing /generate for creepy-brain."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from api.ezaudio import EzAudio as EzAudioModel

WAV_MEDIA_TYPE = "audio/wav"
MAX_DURATION_SEC = 10.0
MIN_DURATION_SEC = 1.0
SAMPLE_RATE = 24_000

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
    """Request body for /generate endpoint."""

    prompt: str = Field(..., min_length=1, max_length=2000, description="Text description of the sound effect.")
    duration_sec: float = Field(
        5.0,
        ge=MIN_DURATION_SEC,
        le=MAX_DURATION_SEC,
        description="Duration in seconds (1–10s).",
    )
    seed: int = Field(0, ge=0, description="Random seed (0 = random).")
    guidance_scale: float = Field(5.0, ge=1.0, le=20.0, description="Classifier-free guidance scale.")
    ddim_steps: int = Field(50, ge=10, le=200, description="DDIM diffusion steps.")


class ReadinessResponse(BaseModel):
    """Response for /ready (model readiness)."""

    ready: bool = Field(..., description="True when the EzAudio model is loaded.")


# ---------------------------------------------------------------------------
# Model singleton (lazy-loaded)
# ---------------------------------------------------------------------------

_model: EzAudioModel | None = None
_model_lock = asyncio.Lock()
_model_error: str | None = None

# Single-slot GPU execution lock.
_gpu_busy = False


def _get_device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


async def _ensure_model() -> EzAudioModel:
    """Load EzAudio s3_xl if not already loaded."""
    global _model, _model_error
    async with _model_lock:
        if _model is not None:
            return _model
        if _model_error is not None:
            raise RuntimeError(f"Model failed to load: {_model_error}")

        device = _get_device()
        logger.info("Loading EzAudio s3_xl on %s...", device)

        from api.ezaudio import EzAudio  # noqa: PLC0415

        loop = asyncio.get_running_loop()
        try:
            model: EzAudioModel = await loop.run_in_executor(
                None,
                lambda: EzAudio(model_name="s3_xl", device=device),
            )
            _model = model
            logger.info("EzAudio model loaded successfully.")
        except Exception as exc:
            _model_error = str(exc)
            logger.exception("Fatal: model load failed.")
            raise

        return _model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    """Convert 1-D float32 numpy array to WAV bytes (PCM-16)."""
    audio = np.clip(audio, -1.0, 1.0)
    audio_int16 = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, audio_int16, sample_rate, format="wav", subtype="PCM_16")
    return buf.getvalue()


def _generate_sync(
    model: EzAudioModel,
    prompt: str,
    duration_sec: float,
    seed: int,
    guidance_scale: float,
    ddim_steps: int,
) -> bytes:
    """Blocking SFX generation. Must be called in a thread pool."""
    random_seed: int | None = seed if seed > 0 else None
    sr, audio = model.generate_audio(
        text=prompt,
        length=duration_sec,
        guidance_scale=guidance_scale,
        ddim_steps=ddim_steps,
        random_seed=random_seed,
        randomize_seed=(random_seed is None),
    )
    audio_np: np.ndarray = np.squeeze(np.array(audio, dtype=np.float32))
    return _numpy_to_wav_bytes(audio_np, int(sr))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SFX Server",
    description="AI sound effect generation via EzAudio for creepy-brain orchestration",
    version="1.0.0",
)


@app.get("/health")
async def health() -> Response:
    """Liveness/readiness check.

    Returns 200 once the model has loaded successfully, 503 while it is still
    loading or after a fatal load error.  The RunPod lifecycle polls this
    endpoint and only marks the pod READY when it gets 200, so we must not
    return 200 before the model is usable.
    """
    if _model_error is not None:
        return Response(
            content=f'{{"status":"error","error":{_model_error!r}}}',
            media_type="application/json",
            status_code=503,
        )
    if _model is None:
        return Response(
            content='{"status":"loading"}',
            media_type="application/json",
            status_code=503,
        )
    return Response(
        content='{"status":"ok"}',
        media_type="application/json",
        status_code=200,
    )


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness check — 200 when model is loaded, 503 otherwise."""
    is_ready = _model is not None
    body = ReadinessResponse(ready=is_ready).model_dump()
    if _model_error is not None:
        body["error"] = _model_error
    http_status = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=body, status_code=http_status)


@app.post("/generate")
async def generate(request: GenerateRequest) -> Response:
    """Generate a sound effect from a text prompt. Returns raw WAV bytes (PCM-16, 24kHz)."""
    global _gpu_busy
    if _gpu_busy:
        raise HTTPException(status_code=503, detail="GPU is busy processing another request. Please retry.")
    _gpu_busy = True
    try:
        model = await _ensure_model()
        loop = asyncio.get_running_loop()
        try:
            wav_bytes = await loop.run_in_executor(
                None,
                lambda: _generate_sync(
                    model=model,
                    prompt=request.prompt,
                    duration_sec=request.duration_sec,
                    seed=request.seed,
                    guidance_scale=request.guidance_scale,
                    ddim_steps=request.ddim_steps,
                ),
            )
        except Exception as exc:
            logger.exception("SFX generation failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _gpu_busy = False
    return Response(content=wav_bytes, media_type=WAV_MEDIA_TYPE)


@app.on_event("startup")
async def startup_event() -> None:
    """Pre-warm model in background on startup."""
    logger.info("Starting SFX server on port 8008...")
    asyncio.create_task(_ensure_model())


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

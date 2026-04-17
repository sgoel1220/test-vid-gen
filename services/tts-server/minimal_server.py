"""Minimal TTS server exposing only /synthesize for creepy-brain orchestration."""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REFERENCE_AUDIO_PATH = Path("/app/reference_audio")
MODEL_CACHE_PATH = Path("/app/model_cache")

# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    """Request body for /synthesize endpoint."""

    text: str = Field(..., min_length=1, max_length=2000, description="Text to synthesize.")
    voice: str = Field(..., description="Reference audio filename for voice cloning.")
    seed: int = Field(0, ge=0, description="Random seed for reproducibility. 0 means random.")

    # Chatterbox generation parameters (all optional with defaults)
    exaggeration: float = Field(0.5, ge=0.0, le=2.0, description="Emotion: 0=flat, 1=normal, 2=exaggerated.")
    cfg_weight: float = Field(0.5, ge=0.0, le=1.0, description="Pacing: high=monotone, low=expressive.")
    temperature: float = Field(0.8, ge=0.0, le=2.0, description="Randomness/variety in generation.")
    repetition_penalty: float = Field(1.2, ge=1.0, le=5.0, description="Penalize repeated tokens.")
    min_p: float = Field(0.05, ge=0.0, le=1.0, description="Min probability threshold.")
    top_p: float = Field(1.0, ge=0.0, le=1.0, description="Nucleus sampling threshold.")


# ---------------------------------------------------------------------------
# TTS Engine (lazy-loaded singleton)
# ---------------------------------------------------------------------------
_model: object | None = None
_model_lock = asyncio.Lock()


def _get_device() -> str:
    """Determine the best available device."""
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


async def _ensure_model_loaded() -> object:
    """Load the TTS model if not already loaded."""
    global _model
    async with _model_lock:
        if _model is not None:
            return _model

        logger.info("Loading Chatterbox TTS model...")
        device = _get_device()
        logger.info("Using device: %s", device)

        # Import here to avoid loading torch modules before device detection
        from chatterbox.tts import ChatterboxTTS

        loop = asyncio.get_running_loop()
        _model = await loop.run_in_executor(
            None,
            lambda: ChatterboxTTS.from_pretrained(device=device),
        )
        logger.info("Chatterbox TTS model loaded successfully")
        return _model


def _synthesize_sync(
    model: object,
    text: str,
    audio_prompt_path: str,
    seed: int,
    exaggeration: float,
    cfg_weight: float,
    temperature: float,
    repetition_penalty: float,
    min_p: float,
    top_p: float,
) -> tuple[torch.Tensor, int]:
    """Synchronous synthesis using Chatterbox."""
    from chatterbox.tts import ChatterboxTTS

    assert isinstance(model, ChatterboxTTS)

    # Set seed for reproducibility
    if seed > 0:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    wav = model.generate(
        text,
        audio_prompt_path=audio_prompt_path,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        min_p=min_p,
        top_p=top_p,
    )
    return wav, model.sr


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Minimal TTS Server",
    description="Stateless TTS synthesis for creepy-brain orchestration",
    version="1.0.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, bool]:
    """Readiness check - returns true when model is loaded."""
    return {"ready": _model is not None}


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest) -> Response:
    """Stateless single-shot TTS synthesis. Returns raw WAV bytes."""
    # Validate voice file exists
    ref_path = (REFERENCE_AUDIO_PATH / request.voice).resolve()
    if not str(ref_path).startswith(str(REFERENCE_AUDIO_PATH.resolve()) + "/"):
        raise HTTPException(status_code=400, detail="Invalid voice filename.")
    if not ref_path.is_file():
        raise HTTPException(status_code=404, detail=f"Voice '{request.voice}' not found.")

    # Ensure model is loaded
    model = await _ensure_model_loaded()

    # Run synthesis in thread pool
    loop = asyncio.get_running_loop()
    wav_tensor, sample_rate = await loop.run_in_executor(
        None,
        lambda: _synthesize_sync(
            model=model,
            text=request.text,
            audio_prompt_path=str(ref_path),
            seed=request.seed,
            exaggeration=request.exaggeration,
            cfg_weight=request.cfg_weight,
            temperature=request.temperature,
            repetition_penalty=request.repetition_penalty,
            min_p=request.min_p,
            top_p=request.top_p,
        ),
    )

    if wav_tensor is None:
        raise HTTPException(status_code=500, detail="Synthesis failed.")

    # Convert to WAV bytes
    audio_np: np.ndarray = wav_tensor.squeeze().cpu().numpy()
    audio_int16 = (np.clip(audio_np, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, audio_int16, sample_rate, format="wav", subtype="PCM_16")

    return Response(content=buf.getvalue(), media_type="audio/wav")


@app.on_event("startup")
async def startup_event() -> None:
    """Pre-load model on startup."""
    logger.info("Starting minimal TTS server...")
    # Start model loading in background
    asyncio.create_task(_ensure_model_loaded())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "minimal_server:app",
        host="0.0.0.0",
        port=8005,
        log_level="info",
    )

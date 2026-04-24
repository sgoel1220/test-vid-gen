"""Minimal music-generation server exposing /generate and /outpaint for creepy-brain."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Literal

import numpy as np
import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler

WAV_MEDIA_TYPE = "audio/wav"
MAX_DURATION_SEC = 600.0
ACE_STEP_ROOT = Path("/app/ace-step")

# ~15 MB decoded ≈ 113 s of stereo PCM-16 at 48 kHz — generous for a context clip.
_MAX_AUDIO_CONTEXT_B64_LEN = 20_000_000

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

    prompt: str = Field(..., min_length=1, max_length=2000, description="Text description of the music.")
    duration_sec: float = Field(..., gt=0.0, le=MAX_DURATION_SEC, description="Duration in seconds (max 600).")
    seed: int = Field(0, ge=0, description="Random seed (0 = random).")
    inference_steps: int = Field(8, ge=1, le=100, description="Diffusion steps.")
    lyrics: str = Field("[Instrumental]", max_length=5000, description="Optional lyrics (use [Instrumental] for no vocals).")


class OutpaintRequest(BaseModel):
    """Request body for /outpaint endpoint."""

    prompt: str = Field(..., min_length=1, max_length=2000, description="Text description for the continuation.")
    audio_context: str = Field(
        ...,
        max_length=_MAX_AUDIO_CONTEXT_B64_LEN,
        description="Base64-encoded WAV bytes of existing audio to continue from.",
    )
    duration_sec: float = Field(
        ..., gt=0.0, le=MAX_DURATION_SEC, description="Seconds of new audio to add after the context clip."
    )
    seed: int = Field(0, ge=0, description="Random seed (0 = random).")
    inference_steps: int = Field(8, ge=1, le=100, description="Diffusion steps.")
    lyrics: str = Field("[Instrumental]", max_length=5000, description="Optional lyrics.")
    crossfade_sec: float = Field(0.25, ge=0.0, le=5.0, description="Crossfade seconds at the continuation seam.")


class HealthResponse(BaseModel):
    """Response for /health (liveness only)."""

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Response for /ready (model readiness)."""

    ready: bool = Field(..., description="True when both DiT and LLM models are loaded.")


# ---------------------------------------------------------------------------
# Model singletons (lazy-loaded)
# ---------------------------------------------------------------------------

_dit: AceStepHandler | None = None
_llm: LLMHandler | None = None
_model_lock = asyncio.Lock()
_model_error: str | None = None  # set on fatal load failure

# Single-slot GPU execution lock: prevents concurrent generation calls from
# racing shared model state and exhausting GPU memory.
# asyncio.Lock makes check-and-acquire atomic (plain bool is not safe).
_gpu_lock = asyncio.Lock()


def _get_device() -> str:
    """Return best available device.  Aborts if CUDA is expected but missing."""
    if torch.cuda.is_available():
        return "cuda"
    logger.critical("CUDA is not available — aborting to avoid billing a GPU pod for CPU inference.")
    raise SystemExit(1)


async def _ensure_models() -> tuple[AceStepHandler, LLMHandler]:
    """Load ACE-Step 1.5 DiT + LLM handlers if not already loaded."""
    global _dit, _llm, _model_error
    async with _model_lock:
        if _dit is not None and _llm is not None:
            return _dit, _llm
        if _model_error is not None:
            raise RuntimeError(f"Model failed to load: {_model_error}")

        device = _get_device()
        logger.info("Loading ACE-Step 1.5 models on %s...", device)

        # Defer imports until load time to avoid pulling in heavy modules early.
        from acestep.handler import AceStepHandler as _AceStepHandler  # noqa: PLC0415
        from acestep.llm_inference import LLMHandler as _LLMHandler  # noqa: PLC0415

        loop = asyncio.get_running_loop()

        try:
            dit = _AceStepHandler()
            msg, ok = await loop.run_in_executor(
                None,
                lambda: dit.initialize_service(
                    project_root=str(ACE_STEP_ROOT),
                    config_path="acestep-v15-turbo",
                    device=device,
                    prefer_source="huggingface",
                ),
            )
            if not ok:
                raise RuntimeError(f"DiT init failed: {msg}")

            checkpoint_dir = ACE_STEP_ROOT / "checkpoints"
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            llm = _LLMHandler()
            msg, ok = await loop.run_in_executor(
                None,
                lambda: llm.initialize(
                    checkpoint_dir=str(checkpoint_dir),
                    lm_model_path="acestep-5Hz-lm-1.7B",
                    backend="vllm",
                    device=device,
                ),
            )
            if not ok:
                raise RuntimeError(f"LLM init failed: {msg}")

            _dit, _llm = dit, llm
            logger.info("ACE-Step 1.5 models loaded successfully.")
        except Exception as exc:
            _model_error = str(exc)
            logger.exception("Fatal: model load failed — exiting to avoid idle billing.")
            raise SystemExit(1) from exc

        return _dit, _llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tensor_to_wav_bytes(audio: torch.Tensor, sample_rate: int) -> bytes:
    """Convert [channels, samples] float32 tensor to WAV bytes (PCM-16)."""
    audio_np: np.ndarray = audio.cpu().numpy()
    if audio_np.ndim == 1:
        audio_np = audio_np[np.newaxis, :]  # ensure [1, samples]
    audio_np = audio_np.T  # soundfile wants [samples, channels]
    audio_int16 = (np.clip(audio_np, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, audio_int16, sample_rate, format="wav", subtype="PCM_16")
    return buf.getvalue()


def _validate_wav_bytes(data: bytes) -> sf.info:  # type: ignore[return]
    """Validate that *data* is a readable WAV file.  Raises ValueError on failure."""
    buf = io.BytesIO(data)
    try:
        return sf.info(buf)
    except Exception as exc:
        raise ValueError(f"audio_context is not valid WAV: {exc}") from exc


def _generate_sync(
    dit: AceStepHandler,
    llm: LLMHandler,
    prompt: str,
    duration_sec: float,
    seed: int,
    inference_steps: int,
    lyrics: str,
) -> bytes:
    """Blocking music generation. Must be called in a thread pool."""
    from acestep.inference import GenerationConfig, GenerationParams, generate_music  # noqa: PLC0415

    tmp_dir = tempfile.mkdtemp(prefix="acestep_gen_")
    try:
        params = GenerationParams(
            task_type="text2music",
            caption=prompt,
            lyrics=lyrics,
            duration=duration_sec,
            inference_steps=inference_steps,
            shift=3.0,
        )
        use_random = seed == 0
        config = GenerationConfig(
            batch_size=1,
            audio_format="wav",
            use_random_seed=use_random,
            seeds=[seed if seed > 0 else 0],
        )
        result = generate_music(dit, llm, params, config, save_dir=tmp_dir)
        if not result.success:
            raise RuntimeError(result.error or "generate_music returned failure")
        if not result.audios:
            raise RuntimeError("generate_music returned no audio")
        audio: torch.Tensor = result.audios[0]["tensor"]
        sr: int = result.audios[0]["sample_rate"]
        return _tensor_to_wav_bytes(audio, sr)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _outpaint_sync(
    dit: AceStepHandler,
    llm: LLMHandler,
    audio_wav_bytes: bytes,
    prompt: str,
    duration_sec: float,
    seed: int,
    inference_steps: int,
    lyrics: str,
    crossfade_sec: float,
) -> bytes:
    """Blocking audio continuation. Must be called in a thread pool."""
    from acestep.inference import GenerationConfig, GenerationParams, generate_music  # noqa: PLC0415

    tmp_dir = tempfile.mkdtemp(prefix="acestep_out_")
    try:
        ctx_path = Path(tmp_dir) / "context.wav"
        ctx_path.write_bytes(audio_wav_bytes)

        info = sf.info(str(ctx_path))
        context_sec: float = info.frames / info.samplerate

        use_random = seed == 0
        params = GenerationParams(
            task_type="repaint",
            src_audio=str(ctx_path),
            caption=prompt,
            lyrics=lyrics,
            repainting_start=context_sec,
            repainting_end=context_sec + duration_sec,
            inference_steps=inference_steps,
            shift=3.0,
            repaint_wav_crossfade_sec=crossfade_sec,
        )
        config = GenerationConfig(
            batch_size=1,
            audio_format="wav",
            use_random_seed=use_random,
            seeds=[seed if seed > 0 else 0],
        )
        result = generate_music(dit, llm, params, config, save_dir=tmp_dir)
        if not result.success:
            raise RuntimeError(result.error or "generate_music returned failure")
        if not result.audios:
            raise RuntimeError("generate_music returned no audio")
        audio: torch.Tensor = result.audios[0]["tensor"]
        sr: int = result.audios[0]["sample_rate"]
        return _tensor_to_wav_bytes(audio, sr)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Eager-load models on startup so /health returns 200 once ready."""
    asyncio.ensure_future(_ensure_models())
    yield


app = FastAPI(
    title="Music Server",
    description="AI music generation via ACE-Step 1.5 for creepy-brain orchestration",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> Response:
    """Liveness check — returns 200 once models are loaded, 503 otherwise.

    The Docker HEALTHCHECK polls /ready (not /health) for the same semantics.
    """
    if _model_error is not None:
        return Response(
            content=json.dumps({"status": "error", "error": _model_error}),
            media_type="application/json",
            status_code=503,
        )
    if _dit is None or _llm is None:
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
    """Readiness check — 200 when both models are loaded, 503 otherwise."""
    is_ready = _dit is not None and _llm is not None
    body = ReadinessResponse(ready=is_ready).model_dump()
    if _model_error is not None:
        body["error"] = _model_error
    http_status = status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=body, status_code=http_status)


@app.post("/generate")
async def generate(request: GenerateRequest) -> Response:
    """Generate music from a text prompt. Returns raw WAV bytes (PCM-16, 48kHz)."""
    if _gpu_lock.locked():
        raise HTTPException(status_code=503, detail="GPU is busy processing another request. Please retry.")
    async with _gpu_lock:
        dit, llm = await _ensure_models()
        loop = asyncio.get_running_loop()
        try:
            wav_bytes = await loop.run_in_executor(
                None,
                lambda: _generate_sync(
                    dit=dit,
                    llm=llm,
                    prompt=request.prompt,
                    duration_sec=request.duration_sec,
                    seed=request.seed,
                    inference_steps=request.inference_steps,
                    lyrics=request.lyrics,
                ),
            )
        except Exception as exc:
            logger.exception("Generation failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(content=wav_bytes, media_type=WAV_MEDIA_TYPE)


@app.post("/outpaint")
async def outpaint(request: OutpaintRequest) -> Response:
    """Continue existing audio. Returns raw WAV bytes of the full extended track."""
    # Decode and validate before touching the GPU.
    try:
        audio_wav_bytes = base64.b64decode(request.audio_context, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 in audio_context.") from exc

    try:
        _validate_wav_bytes(audio_wav_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if _gpu_lock.locked():
        raise HTTPException(status_code=503, detail="GPU is busy processing another request. Please retry.")
    async with _gpu_lock:
        dit, llm = await _ensure_models()
        loop = asyncio.get_running_loop()
        try:
            wav_bytes = await loop.run_in_executor(
                None,
                lambda: _outpaint_sync(
                    dit=dit,
                    llm=llm,
                    audio_wav_bytes=audio_wav_bytes,
                    prompt=request.prompt,
                    duration_sec=request.duration_sec,
                    seed=request.seed,
                    inference_steps=request.inference_steps,
                    lyrics=request.lyrics,
                    crossfade_sec=request.crossfade_sec,
                ),
            )
        except Exception as exc:
            logger.exception("Outpaint failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(content=wav_bytes, media_type=WAV_MEDIA_TYPE)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8007,
        log_level="info",
    )

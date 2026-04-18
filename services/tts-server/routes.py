"""FastAPI route handlers."""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from typing import Tuple

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from audio.encoding import encode_to_wav_bytes, WAV_MEDIA_TYPE

import engine
from config import config_manager, get_output_path, get_reference_audio_path
from files import get_valid_reference_files, validate_reference_audio, validate_voice_path, ALLOWED_AUDIO_EXTENSIONS
from job_store import job_store
from models import (
    ChunkPreviewRequest,
    ChunkPreviewResponse,
    LiteCloneJobCreatedResponse,
    LiteCloneJobStatusResponse,
    LiteCloneRunResponse,
    LiteCloneTTSRequest,
    ReferenceAudioFilesResponse,
    SynthesizeRequest,
    UploadReferenceAudioResponse,
)
from run_orchestrator import build_chunk_preview, execute_lite_clone_run, run_lite_clone_job
from text import sanitize_filename

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Reference audio upload helpers
# ---------------------------------------------------------------------------

def _resolve_upload_filename(filename: str | None) -> str:
    if not filename:
        raise ValueError("File received with no filename.")
    safe = sanitize_filename(filename)
    if not safe.lower().endswith(ALLOWED_AUDIO_EXTENSIONS):
        raise ValueError("Invalid file type. Only .wav and .mp3 are allowed.")
    return safe


async def _save_uploaded_reference(file: UploadFile) -> Tuple[str, bool]:
    safe_name = _resolve_upload_filename(file.filename)
    ref_dir = get_reference_audio_path(ensure_absolute=True)
    ref_dir.mkdir(parents=True, exist_ok=True)
    dest = ref_dir / safe_name
    created = False
    try:
        if dest.exists():
            return safe_name, True
        with dest.open("wb") as buf:
            created = True
            shutil.copyfileobj(file.file, buf)
        max_dur = config_manager.get_int("audio_output.max_reference_duration_sec", 30)
        ok, msg = validate_reference_audio(dest, max_dur)
        if not ok:
            dest.unlink(missing_ok=True)
            raise ValueError(msg)
        return safe_name, False
    except Exception:
        if created:
            dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


# ---------------------------------------------------------------------------
# Static UI routes (served separately by app.py via FileResponse)
# ---------------------------------------------------------------------------

@router.get("/api/model-info")
async def model_info() -> engine.ModelInfo:
    return engine.get_model_info()


@router.get("/api/reference-audio")
async def reference_audio_files() -> ReferenceAudioFilesResponse:
    return ReferenceAudioFilesResponse(files=get_valid_reference_files())


@router.post("/api/reference-audio/upload")
async def upload_reference_audio(file: UploadFile = File(...)) -> UploadReferenceAudioResponse:
    try:
        uploaded_filename, already_exists = await _save_uploaded_reference(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to upload reference audio.", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    message = (
        f"Reference audio '{uploaded_filename}' already exists."
        if already_exists
        else f"Uploaded reference audio '{uploaded_filename}'."
    )
    return UploadReferenceAudioResponse(
        message=message,
        uploaded_file=uploaded_filename,
        already_exists=already_exists,
        all_reference_files=get_valid_reference_files(),
    )


@router.post("/api/chunks/preview", response_model=ChunkPreviewResponse)
async def chunk_preview(request: ChunkPreviewRequest) -> ChunkPreviewResponse:
    preview = build_chunk_preview(request.text, request.split_text, request.chunk_size)
    if not preview:
        raise HTTPException(status_code=400, detail="No text chunks were produced.")
    return ChunkPreviewResponse(chunk_count=len(preview), chunks=preview)


@router.post("/tts", response_model=LiteCloneRunResponse)
async def tts_endpoint(request: LiteCloneTTSRequest) -> LiteCloneRunResponse:
    return await execute_lite_clone_run(request)


@router.post("/api/jobs", response_model=LiteCloneJobCreatedResponse)
async def create_job(request: LiteCloneTTSRequest) -> LiteCloneJobCreatedResponse:
    job_id = uuid.uuid4().hex
    job_store.create(job_id, request.selected_chunk_indices)
    asyncio.create_task(run_lite_clone_job(job_id, request))
    return LiteCloneJobCreatedResponse(job_id=job_id, status_url=job_store.status_url(job_id))


@router.get("/api/jobs/{job_id}", response_model=LiteCloneJobStatusResponse)
async def get_job(job_id: str) -> LiteCloneJobStatusResponse:
    return job_store.get(job_id)


@router.post("/synthesize")
async def synthesize_endpoint(request: SynthesizeRequest) -> Response:
    """Stateless single-shot TTS synthesis. Returns raw WAV bytes."""
    try:
        ref_path = validate_voice_path(request.voice, get_reference_audio_path(ensure_absolute=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    loop = asyncio.get_running_loop()
    wav_tensor, sample_rate = await loop.run_in_executor(
        None,
        lambda: engine.synthesize(
            text=request.text,
            audio_prompt_path=str(ref_path),
            seed=request.seed,
        ),
    )

    if wav_tensor is None or sample_rate is None:
        raise HTTPException(status_code=500, detail="Synthesis failed.")

    return Response(content=encode_to_wav_bytes(wav_tensor, sample_rate), media_type=WAV_MEDIA_TYPE)

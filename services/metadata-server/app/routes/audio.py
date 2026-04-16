"""Audio upload and streaming endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from creepy_pasta_protocol.audio import AudioBlobDTO, UploadChunkAudioMetadata, UploadFinalAudioMetadata
from creepy_pasta_protocol.common import AudioFormat, StorageBackend
from creepy_pasta_protocol.validation import ChunkValidationSnapshot

from app.auth import require_api_key
from app.converters import audio_blob_to_dto
from app.db import DbSession
from app.services import audio as audio_svc
from app.storage import get_audio_store

router = APIRouter(tags=["audio"], dependencies=[Depends(require_api_key)])


@router.post(
    "/v1/runs/{run_id}/chunks/{chunk_index}/audio",
    response_model=AudioBlobDTO,
)
async def upload_chunk_audio(
    run_id: uuid.UUID,
    chunk_index: int,
    session: DbSession,
    file: UploadFile,
    format: AudioFormat = Form(...),
    sample_rate: int = Form(...),
    duration_sec: float = Form(...),
    mime_type: str = Form(...),
    attempts_used: int = Form(0),
    validation_json: Optional[str] = Form(None),
) -> AudioBlobDTO:
    validation: Optional[ChunkValidationSnapshot] = None
    if validation_json is not None:
        validation = ChunkValidationSnapshot.model_validate(json.loads(validation_json))

    audio_store = get_audio_store()
    data = await file.read()
    storage_key, sha256, byte_size = await audio_store.put(data, format.value)

    metadata = UploadChunkAudioMetadata(
        run_id=str(run_id),
        chunk_index=chunk_index,
        sha256=sha256,
        byte_size=byte_size,
        format=format,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        storage_backend=StorageBackend.LOCAL,
        storage_key=storage_key,
        mime_type=mime_type,
    )
    blob = await audio_svc.store_chunk_audio(
        session,
        run_id,
        chunk_index,
        data,
        metadata,
        attempts_used=attempts_used,
        validation=validation,
    )
    await session.commit()
    return audio_blob_to_dto(blob)


@router.post(
    "/v1/runs/{run_id}/final_audio",
    response_model=AudioBlobDTO,
)
async def upload_final_audio(
    run_id: uuid.UUID,
    session: DbSession,
    file: UploadFile,
    format: AudioFormat = Form(...),
    sample_rate: int = Form(...),
    duration_sec: float = Form(...),
    mime_type: str = Form(...),
) -> AudioBlobDTO:
    audio_store = get_audio_store()
    data = await file.read()
    storage_key, sha256, byte_size = await audio_store.put(data, format.value)

    metadata = UploadFinalAudioMetadata(
        run_id=str(run_id),
        sha256=sha256,
        byte_size=byte_size,
        format=format,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        storage_backend=StorageBackend.LOCAL,
        storage_key=storage_key,
        mime_type=mime_type,
    )
    blob = await audio_svc.store_final_audio(session, run_id, data, metadata)
    await session.commit()
    return audio_blob_to_dto(blob)


@router.get("/v1/audio/{audio_blob_id}")
async def stream_audio(
    audio_blob_id: uuid.UUID,
    session: DbSession,
) -> StreamingResponse:
    try:
        blob = await audio_svc.get_blob_for_streaming(session, audio_blob_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audio blob not found.",
        )
    audio_store = get_audio_store()
    return StreamingResponse(
        content=audio_store.stream(blob.storage_key),
        media_type=blob.mime_type,
        headers={"Content-Length": str(blob.byte_size)},
    )

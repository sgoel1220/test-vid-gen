"""Pod-run-id-based alias routes for runs, chunks, and audio.

These routes accept pod_run_id (e.g. '20260416_014500__Robert__abc123ef')
instead of UUID, resolve it to the server UUID, then delegate to existing services.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import StreamingResponse

from creepy_pasta_protocol.audio import AudioBlobDTO, UploadChunkAudioMetadata, UploadFinalAudioMetadata
from creepy_pasta_protocol.chunks import ChunkDTO, ChunkSpec
from creepy_pasta_protocol.common import AudioFormat, StorageBackend
from creepy_pasta_protocol.runs import PatchRunRequest, RunDetailDTO
from creepy_pasta_protocol.validation import ChunkValidationSnapshot

from app.auth import require_api_key
from app.converters import audio_blob_to_dto, chunk_to_dto, run_to_detail
from app.db import DbSession
from app.services import audio as audio_svc
from app.services import chunks as chunks_svc
from app.services import runs as runs_svc
from app.storage import get_audio_store

router = APIRouter(
    prefix="/v1/runs/by-pod",
    tags=["runs-by-pod"],
    dependencies=[Depends(require_api_key)],
)


@router.patch("/{pod_run_id}", response_model=RunDetailDTO)
async def patch_run_by_pod(
    pod_run_id: str,
    body: PatchRunRequest,
    session: DbSession,
) -> RunDetailDTO:
    """PATCH /v1/runs/by-pod/{pod_run_id} — resolve and delegate to runs_svc.patch."""
    run = await runs_svc.get_by_pod_run_id(session, pod_run_id)
    await runs_svc.patch(session, run.id, body)
    detail = await runs_svc.get_detail(session, run.id)
    await session.commit()
    return run_to_detail(detail)


@router.post("/{pod_run_id}/chunks", response_model=list[ChunkDTO])
async def bulk_create_chunks_by_pod(
    pod_run_id: str,
    specs: list[ChunkSpec],
    session: DbSession,
) -> list[ChunkDTO]:
    """POST /v1/runs/by-pod/{pod_run_id}/chunks — resolve and delegate to chunks_svc.bulk_upsert."""
    run = await runs_svc.get_by_pod_run_id(session, pod_run_id)
    chunks = await chunks_svc.bulk_upsert(session, run.id, specs)
    await session.commit()
    return [chunk_to_dto(c) for c in chunks]


@router.post("/{pod_run_id}/chunks/{chunk_index}/audio", response_model=AudioBlobDTO)
async def upload_chunk_audio_by_pod(
    pod_run_id: str,
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
    """POST /v1/runs/by-pod/{pod_run_id}/chunks/{chunk_index}/audio — resolve and delegate."""
    run = await runs_svc.get_by_pod_run_id(session, pod_run_id)

    validation: Optional[ChunkValidationSnapshot] = None
    if validation_json is not None:
        validation = ChunkValidationSnapshot.model_validate(json.loads(validation_json))

    audio_store = get_audio_store()
    data = await file.read()
    storage_key, sha256, byte_size = await audio_store.put(data, format.value)

    metadata = UploadChunkAudioMetadata(
        run_id=str(run.id),
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
        run.id,
        chunk_index,
        data,
        metadata,
        attempts_used=attempts_used,
        validation=validation,
    )
    await session.commit()
    return audio_blob_to_dto(blob)


@router.post("/{pod_run_id}/final_audio", response_model=AudioBlobDTO)
async def upload_final_audio_by_pod(
    pod_run_id: str,
    session: DbSession,
    file: UploadFile,
    format: AudioFormat = Form(...),
    sample_rate: int = Form(...),
    duration_sec: float = Form(...),
    mime_type: str = Form(...),
) -> AudioBlobDTO:
    """POST /v1/runs/by-pod/{pod_run_id}/final_audio — resolve and delegate."""
    run = await runs_svc.get_by_pod_run_id(session, pod_run_id)

    audio_store = get_audio_store()
    data = await file.read()
    storage_key, sha256, byte_size = await audio_store.put(data, format.value)

    metadata = UploadFinalAudioMetadata(
        run_id=str(run.id),
        sha256=sha256,
        byte_size=byte_size,
        format=format,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        storage_backend=StorageBackend.LOCAL,
        storage_key=storage_key,
        mime_type=mime_type,
    )
    blob = await audio_svc.store_final_audio(session, run.id, data, metadata)
    await session.commit()
    return audio_blob_to_dto(blob)

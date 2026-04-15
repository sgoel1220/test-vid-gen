"""Voice upload and list endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, UploadFile

from creepy_pasta_protocol.common import AudioFormat, StorageBackend
from creepy_pasta_protocol.voices import CreateVoiceResponse, VoiceDTO

from app.auth import require_api_key
from app.converters import audio_blob_to_dto, voice_to_dto
from app.db import DbSession
from app.models import AudioBlob
from app.services import voices as voices_svc
from app.storage import get_audio_store

router = APIRouter(prefix="/v1/voices", tags=["voices"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=CreateVoiceResponse)
async def upload_voice(
    session: DbSession,
    file: UploadFile,
    filename: str = Form(...),
    format: AudioFormat = Form(...),
    sample_rate: int = Form(...),
    duration_sec: float = Form(...),
    mime_type: str = Form(...),
) -> CreateVoiceResponse:
    existing = await voices_svc.get_by_filename(session, filename)
    if existing is not None:
        return CreateVoiceResponse(voice=voice_to_dto(existing), created=False)

    audio_store = get_audio_store()
    data = await file.read()
    storage_key, sha256, byte_size = await audio_store.put(data, format.value)

    blob = AudioBlob(
        id=uuid.uuid4(),
        storage_backend=StorageBackend.LOCAL,
        storage_key=storage_key,
        sha256=sha256,
        byte_size=byte_size,
        mime_type=mime_type,
        format=format,
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        created_at=datetime.now(timezone.utc),
    )
    session.add(blob)
    await session.flush()

    voice = await voices_svc.upsert(session, filename, blob)
    await session.commit()
    return CreateVoiceResponse(voice=voice_to_dto(voice), created=True)


@router.get("", response_model=list[VoiceDTO])
async def list_voices(session: DbSession) -> list[VoiceDTO]:
    voices = await voices_svc.list_all(session)
    return [voice_to_dto(v) for v in voices]

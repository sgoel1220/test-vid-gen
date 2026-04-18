"""Voice upload and list endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Form, UploadFile

from app.db import DbSession
from app.models.enums import BlobType
from app.schemas.voice import CreateVoiceResponse, VoiceResponse
from app.services import blob_service, voice_service

router = APIRouter(prefix="/api/voices", tags=["voices"])


@router.post("", response_model=CreateVoiceResponse)
async def upload_voice(
    session: DbSession,
    file: UploadFile,
    name: str = Form(...),
    mime_type: str = Form(...),
    description: str = Form(""),
    is_default: bool = Form(False),
) -> CreateVoiceResponse:
    """Upload a voice reference audio file.

    Idempotent: if a voice with the same name already exists the existing record
    is returned with ``created=False``.
    """
    existing = await voice_service.get_by_name(session, name)
    if existing is not None:
        return CreateVoiceResponse(voice=VoiceResponse.model_validate(existing), created=False)

    data = await file.read()
    blob = await blob_service.store(
        session,
        data=data,
        mime_type=mime_type,
        blob_type=BlobType.VOICE_AUDIO,
    )

    voice_result = await voice_service.get_or_create(
        session,
        name=name,
        audio_path=str(blob.id),
        description=description or None,
        is_default=is_default,
    )
    await session.commit()
    return CreateVoiceResponse(
        voice=VoiceResponse.model_validate(voice_result.voice),
        created=voice_result.created,
    )


@router.get("", response_model=list[VoiceResponse])
async def list_voices(session: DbSession) -> list[VoiceResponse]:
    voices = await voice_service.list_all(session)
    return [VoiceResponse.model_validate(v) for v in voices]

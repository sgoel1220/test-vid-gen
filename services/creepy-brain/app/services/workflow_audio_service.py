"""Workflow audio encoding operations."""

from __future__ import annotations

import io
import uuid
from importlib import import_module
from typing import Any

import soundfile as sf
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audio.encoding import encode_wav_to_mp3
from app.schemas.workflow import EncodeMp3Response
from app.services import blob_service
from app.workflows.db_helpers import get_session_maker

_enums: Any = import_module("app.models.enums")
_workflow_models: Any = import_module("app.models.workflow")
BlobType: Any = _enums.BlobType
WorkflowChunk: Any = _workflow_models.WorkflowChunk

log = structlog.get_logger()


async def encode_chunks_to_mp3(
    workflow_id: uuid.UUID,
    db: AsyncSession,
) -> EncodeMp3Response:
    """Encode WAV chunk blobs to MP3 in-place, without re-running TTS.

    Finds all chunks for this workflow that have a WAV blob (tts_audio_blob_id)
    but no MP3 blob (tts_mp3_blob_id = NULL), encodes them locally using ffmpeg,
    stores the MP3 as a new blob, and writes tts_mp3_blob_id back to the chunk row.
    """
    session_maker = get_session_maker()

    result = await db.execute(
        select(WorkflowChunk).where(
            WorkflowChunk.workflow_id == workflow_id,
            WorkflowChunk.tts_audio_blob_id.is_not(None),
            WorkflowChunk.tts_mp3_blob_id.is_(None),
        )
    )
    chunks = list(result.scalars().all())

    if not chunks:
        return EncodeMp3Response(encoded=0, skipped=0)

    encoded = 0
    skipped = 0

    for chunk in chunks:
        try:
            if chunk.tts_audio_blob_id is None:
                log.warning("encode_mp3: chunk %d has no audio blob, skipping", chunk.chunk_index)
                skipped += 1
                continue

            async with session_maker() as session:
                wav_blob = await blob_service.get(session, chunk.tts_audio_blob_id)
                audio, sr = sf.read(io.BytesIO(wav_blob.data), dtype="float32")
                mp3_bytes = await encode_wav_to_mp3(audio, sr)
                mp3_blob = await blob_service.store(
                    session=session,
                    data=mp3_bytes,
                    mime_type="audio/mpeg",
                    blob_type=BlobType.CHUNK_AUDIO_MP3,
                    workflow_id=workflow_id,
                )
                await session.commit()

            chunk.tts_mp3_blob_id = mp3_blob.id
            await db.flush()
            encoded += 1
            log.info(
                "encode_mp3: chunk %d encoded mp3_blob_id=%s",
                chunk.chunk_index,
                mp3_blob.id,
            )
        except Exception:
            log.exception("encode_mp3: failed chunk %d", chunk.chunk_index)
            skipped += 1

    await db.commit()
    return EncodeMp3Response(encoded=encoded, skipped=skipped)

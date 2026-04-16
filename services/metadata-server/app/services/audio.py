"""Audio service — persist AudioBlob records and link them to chunks / runs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from creepy_pasta_protocol.audio import UploadChunkAudioMetadata, UploadFinalAudioMetadata
from creepy_pasta_protocol.validation import ChunkValidationSnapshot

from app.models import AudioBlob, Chunk, Run


async def store_chunk_audio(
    session: AsyncSession,
    run_id: uuid.UUID,
    chunk_index: int,
    data: bytes,  # reserved for future integrity checks; storage is handled by the route
    metadata: UploadChunkAudioMetadata,
    attempts_used: int = 0,
    validation: Optional[ChunkValidationSnapshot] = None,
) -> AudioBlob:
    """Persist an AudioBlob for a chunk and link it to the Chunk row.

    Idempotent: if an AudioBlob with the same SHA256 already exists, reuse it
    instead of creating a duplicate.
    """
    # Check for existing chunk with audio already linked (idempotency for retries)
    result = await session.execute(
        select(Chunk).where(
            Chunk.run_id == run_id,
            Chunk.chunk_index == chunk_index,
        )
    )
    chunk = result.scalar_one()
    if chunk.audio_blob_id is not None:
        # Chunk already has audio - fetch and return existing blob
        existing_result = await session.execute(
            select(AudioBlob).where(AudioBlob.id == chunk.audio_blob_id)
        )
        return existing_result.scalar_one()

    # Check for existing blob with same SHA256 (content deduplication)
    result = await session.execute(
        select(AudioBlob).where(AudioBlob.sha256 == metadata.sha256)
    )
    blob = result.scalar_one_or_none()

    if blob is None:
        # Create new blob
        blob = AudioBlob(
            id=uuid.uuid4(),
            storage_backend=metadata.storage_backend,
            storage_key=metadata.storage_key,
            sha256=metadata.sha256,
            byte_size=metadata.byte_size,
            mime_type=metadata.mime_type,
            format=metadata.format,
            sample_rate=metadata.sample_rate,
            duration_sec=metadata.duration_sec,
            created_at=datetime.now(timezone.utc),
        )
        session.add(blob)
        await session.flush()

    # Link blob to chunk
    chunk.audio_blob_id = blob.id
    chunk.attempts_used = attempts_used
    chunk.validation = validation
    await session.flush()
    return blob


async def store_final_audio(
    session: AsyncSession,
    run_id: uuid.UUID,
    data: bytes,  # reserved for future integrity checks; storage is handled by the route
    metadata: UploadFinalAudioMetadata,
) -> AudioBlob:
    """Persist an AudioBlob for the final stitched audio and link it to the Run row.

    Idempotent: if the run already has final audio, returns the existing blob
    instead of creating a duplicate. Also deduplicates blobs by SHA256.
    """
    # Check if run already has final audio (idempotency for retries)
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one()
    if run.final_audio_id is not None:
        # Run already has final audio - fetch and return existing blob
        existing_result = await session.execute(
            select(AudioBlob).where(AudioBlob.id == run.final_audio_id)
        )
        return existing_result.scalar_one()

    # Check for existing blob with same SHA256 (content deduplication)
    result = await session.execute(
        select(AudioBlob).where(AudioBlob.sha256 == metadata.sha256)
    )
    blob = result.scalar_one_or_none()

    if blob is None:
        # Create new blob
        blob = AudioBlob(
            id=uuid.uuid4(),
            storage_backend=metadata.storage_backend,
            storage_key=metadata.storage_key,
            sha256=metadata.sha256,
            byte_size=metadata.byte_size,
            mime_type=metadata.mime_type,
            format=metadata.format,
            sample_rate=metadata.sample_rate,
            duration_sec=metadata.duration_sec,
            created_at=datetime.now(timezone.utc),
        )
        session.add(blob)
        await session.flush()

    # Link blob to run
    run.final_audio_id = blob.id
    await session.flush()
    return blob


async def get_blob_for_streaming(
    session: AsyncSession, audio_blob_id: uuid.UUID
) -> AudioBlob:
    """Fetch an AudioBlob by PK; raises NoResultFound if absent."""
    result = await session.execute(
        select(AudioBlob).where(AudioBlob.id == audio_blob_id)
    )
    return result.scalar_one()

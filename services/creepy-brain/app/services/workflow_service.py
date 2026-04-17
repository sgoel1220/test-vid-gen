"""WorkflowChunk CRUD operations for TTS step progress tracking.

Transaction ownership convention
---------------------------------
Service methods only *flush* — they never commit.  The caller is responsible
for ``await session.commit()`` after each logical unit of work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ChunkStatus
from app.models.workflow import WorkflowChunk


class WorkflowService:
    """WorkflowChunk database operations for tracking per-chunk TTS progress."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_chunk(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        chunk_text: str,
    ) -> WorkflowChunk:
        """Create or retrieve the WorkflowChunk for *chunk_index* (flush only)."""
        result = await self._session.execute(
            select(WorkflowChunk).where(
                WorkflowChunk.workflow_id == workflow_id,
                WorkflowChunk.chunk_index == chunk_index,
            )
        )
        chunk = result.scalar_one_or_none()
        if chunk is None:
            chunk = WorkflowChunk(
                workflow_id=workflow_id,
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                tts_status=ChunkStatus.PENDING,
                image_status=ChunkStatus.PENDING,
            )
            self._session.add(chunk)
            await self._session.flush()
            await self._session.refresh(chunk)
        return chunk

    async def mark_chunk_processing(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
    ) -> None:
        """Set tts_status to PROCESSING (flush only)."""
        chunk = await self._get_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.PROCESSING
        await self._session.flush()

    async def complete_chunk_tts(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        blob_id: uuid.UUID,
        duration_sec: float,
        attempts_used: int,
    ) -> None:
        """Record successful TTS for a chunk (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            chunk_index: Zero-based chunk position.
            blob_id: UUID of the saved WAV blob in ``workflow_blobs``.
            duration_sec: Audio duration in seconds.
            attempts_used: Number of synthesis attempts made (1 = first try succeeded).
        """
        chunk = await self._get_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.COMPLETED
        chunk.tts_audio_blob_id = blob_id
        chunk.tts_duration_sec = duration_sec
        chunk.tts_completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def fail_chunk_tts(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        blob_id: uuid.UUID,
        attempts_used: int,
    ) -> None:
        """Record a best-effort TTS chunk where all validation attempts failed (flush only).

        The best-effort WAV blob is still saved so downstream steps have *something*
        to work with, but the chunk is marked FAILED so operators can identify it.

        Args:
            workflow_id: The owning workflow UUID.
            chunk_index: Zero-based chunk position.
            blob_id: UUID of the saved (unvalidated) WAV blob in ``workflow_blobs``.
            attempts_used: Total synthesis attempts made.
        """
        chunk = await self._get_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.FAILED
        chunk.tts_audio_blob_id = blob_id
        chunk.tts_completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def _get_or_raise(
        self, workflow_id: uuid.UUID, chunk_index: int
    ) -> WorkflowChunk:
        result = await self._session.execute(
            select(WorkflowChunk).where(
                WorkflowChunk.workflow_id == workflow_id,
                WorkflowChunk.chunk_index == chunk_index,
            )
        )
        chunk = result.scalar_one_or_none()
        if chunk is None:
            raise ValueError(
                f"WorkflowChunk not found: workflow_id={workflow_id} chunk_index={chunk_index}"
            )
        return chunk


async def get_chunks_for_image_step(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> list[dict[str, object]]:
    """Return chunk data needed by the image_generation step.

    Args:
        session: Active SQLAlchemy async session.
        workflow_id: The workflow whose chunks to fetch.

    Returns:
        List of dicts with ``index``, ``text``, and ``blob_id`` keys.
    """
    result = await session.execute(
        select(WorkflowChunk)
        .where(WorkflowChunk.workflow_id == workflow_id)
        .order_by(WorkflowChunk.chunk_index)
    )
    chunks = result.scalars().all()
    return [
        {
            "index": c.chunk_index,
            "text": c.chunk_text,
            "blob_id": str(c.tts_audio_blob_id) if c.tts_audio_blob_id else None,
        }
        for c in chunks
    ]


def get_optional_workflow_id(workflow_run_id: str) -> Optional[uuid.UUID]:
    """Parse the Hatchet workflow run ID string to a UUID, or return None on failure."""
    try:
        return uuid.UUID(workflow_run_id)
    except ValueError:
        return None

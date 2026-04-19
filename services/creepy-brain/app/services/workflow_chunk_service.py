"""Workflow chunk CRUD operations.

Transaction ownership convention
---------------------------------
Service methods only *flush* -- they never commit. The caller is responsible
for ``await session.commit()`` after each logical unit of work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_enums: Any = import_module("app.models.enums")
_workflow_models: Any = import_module("app.models.workflow")
ChunkStatus: Any = _enums.ChunkStatus
WorkflowChunk: Any = _workflow_models.WorkflowChunk


class WorkflowChunkService:
    """Database operations for workflow TTS chunks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_chunk(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        chunk_text: str,
    ) -> WorkflowChunk:
        """Create or update the WorkflowChunk for *chunk_index* (flush only)."""
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
            )
            self._session.add(chunk)
            await self._session.flush()
            await self._session.refresh(chunk)
        elif chunk.chunk_text != chunk_text:
            chunk.chunk_text = chunk_text
            chunk.tts_status = ChunkStatus.PENDING
            chunk.tts_audio_blob_id = None
            chunk.tts_duration_sec = None
            await self._session.flush()
        return chunk

    async def mark_chunk_processing(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
    ) -> None:
        """Set tts_status to PROCESSING (flush only)."""
        chunk = await self._get_chunk_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.PROCESSING
        await self._session.flush()

    async def complete_chunk_tts(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        blob_id: uuid.UUID,
        duration_sec: float,
        attempts_used: int,
        mp3_blob_id: uuid.UUID | None = None,
    ) -> None:
        """Record successful TTS for a chunk (flush only)."""
        chunk = await self._get_chunk_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.COMPLETED
        chunk.tts_audio_blob_id = blob_id
        chunk.tts_mp3_blob_id = mp3_blob_id
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
        """Record a best-effort failed TTS chunk with its WAV blob (flush only)."""
        chunk = await self._get_chunk_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.FAILED
        chunk.tts_audio_blob_id = blob_id
        chunk.tts_completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def reset_chunks_to_pending(
        self,
        workflow_id: uuid.UUID,
        chunk_indices: list[int] | None = None,
    ) -> int:
        """Reset FAILED chunks back to PENDING so they can be re-synthesized."""
        stmt = select(WorkflowChunk).where(
            WorkflowChunk.workflow_id == workflow_id,
            WorkflowChunk.tts_status == ChunkStatus.FAILED,
        )
        if chunk_indices is not None:
            stmt = stmt.where(WorkflowChunk.chunk_index.in_(chunk_indices))

        result = await self._session.execute(stmt)
        chunks = list(result.scalars().all())

        for chunk in chunks:
            chunk.tts_status = ChunkStatus.PENDING
            chunk.tts_audio_blob_id = None
            chunk.tts_mp3_blob_id = None
            chunk.tts_completed_at = None

        await self._session.flush()
        return len(chunks)

    async def _get_chunk_or_raise(
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

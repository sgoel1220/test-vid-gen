"""Read projections for workflow execution data."""

from __future__ import annotations

import uuid
from importlib import import_module
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_enums: Any = import_module("app.models.enums")
_workflow_models: Any = import_module("app.models.workflow")
ChunkStatus: Any = _enums.ChunkStatus
WorkflowChunk: Any = _workflow_models.WorkflowChunk
WorkflowScene: Any = _workflow_models.WorkflowScene


class ChunkForImageStep(BaseModel):
    """Chunk data needed by image_generation and stitch_final steps."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Zero-based chunk position")
    text: str = Field(description="Chunk text content")
    normalized_text: str | None = Field(default=None, description="Normalized text for TTS (None if not yet computed)")
    blob_id: str | None = Field(description="UUID of the WAV blob (None if TTS failed)")
    tts_status: ChunkStatus = Field(description="Chunk TTS status")
    scene_id: str | None = Field(description="UUID of the linked scene (None if unlinked)")
    duration_sec: float | None = Field(
        default=None, description="TTS audio duration in seconds (None if TTS not done)"
    )


class WorkflowReadRepository:
    """Read-only workflow projections."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_chunks_for_image_step(
        self,
        workflow_id: uuid.UUID,
    ) -> list[ChunkForImageStep]:
        """Return chunk data needed by image_generation and stitch_final."""
        result = await self._session.execute(
            select(WorkflowChunk)
            .where(WorkflowChunk.workflow_id == workflow_id)
            .order_by(WorkflowChunk.chunk_index)
        )
        chunks = result.scalars().all()
        return [
            ChunkForImageStep(
                index=c.chunk_index,
                text=c.chunk_text,
                normalized_text=c.normalized_text,
                blob_id=str(c.tts_audio_blob_id) if c.tts_audio_blob_id else None,
                tts_status=c.tts_status,
                scene_id=str(c.scene_id) if c.scene_id else None,
                duration_sec=c.tts_duration_sec,
            )
            for c in chunks
        ]

    async def get_scenes_for_workflow(
        self,
        workflow_id: uuid.UUID,
    ) -> list[WorkflowScene]:
        """Return all scenes for a workflow ordered by scene_index."""
        result = await self._session.execute(
            select(WorkflowScene)
            .where(WorkflowScene.workflow_id == workflow_id)
            .order_by(WorkflowScene.scene_index)
        )
        return list(result.scalars().all())


async def get_chunks_for_image_step(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> list[ChunkForImageStep]:
    """Return chunk data needed by the image_generation and stitch_final steps."""
    return await WorkflowReadRepository(session).get_chunks_for_image_step(workflow_id)


async def get_scenes_for_workflow(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> list[WorkflowScene]:
    """Return all scenes for a workflow ordered by scene_index."""
    return await WorkflowReadRepository(session).get_scenes_for_workflow(workflow_id)

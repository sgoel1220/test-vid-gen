"""Backward-compatible workflow service facade.

Transaction ownership convention
---------------------------------
Service methods only *flush* -- they never commit. The caller is responsible
for ``await session.commit()`` after each logical unit of work.
"""

from __future__ import annotations

import uuid
from importlib import import_module
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.workflow_chunk_service import WorkflowChunkService
from app.services.workflow_fork_service import WorkflowForkService
from app.services.workflow_ids import get_optional_workflow_id
from app.services.workflow_lifecycle_service import WorkflowLifecycleService
from app.services.workflow_read_repository import (
    ChunkForImageStep,
    WorkflowReadRepository,
)
from app.services.workflow_scene_service import WorkflowSceneService
from app.services.workflow_step_service import WorkflowStepService

_enums: Any = import_module("app.models.enums")
_json_schemas: Any = import_module("app.models.json_schemas")
_workflow_models: Any = import_module("app.models.workflow")
StepName: Any = _enums.StepName
StepOutputSchema: Any = _json_schemas.StepOutputSchema
WorkflowResultSchema: Any = _json_schemas.WorkflowResultSchema
Workflow: Any = _workflow_models.Workflow
WorkflowChunk: Any = _workflow_models.WorkflowChunk
WorkflowScene: Any = _workflow_models.WorkflowScene


class WorkflowService:
    """Backward-compatible facade over focused workflow services."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._chunks = WorkflowChunkService(session)
        self._scenes = WorkflowSceneService(session)
        self._steps = WorkflowStepService(session)
        self._lifecycle = WorkflowLifecycleService(session)

    async def upsert_chunk(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        chunk_text: str,
    ) -> WorkflowChunk:
        """Create or update the WorkflowChunk for *chunk_index* (flush only)."""
        return await self._chunks.upsert_chunk(workflow_id, chunk_index, chunk_text)

    async def mark_chunk_processing(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
    ) -> None:
        """Set tts_status to PROCESSING (flush only)."""
        await self._chunks.mark_chunk_processing(workflow_id, chunk_index)

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
        await self._chunks.complete_chunk_tts(
            workflow_id,
            chunk_index,
            blob_id,
            duration_sec,
            attempts_used,
            mp3_blob_id,
        )

    async def fail_chunk_tts(
        self,
        workflow_id: uuid.UUID,
        chunk_index: int,
        blob_id: uuid.UUID,
        attempts_used: int,
    ) -> None:
        """Record a best-effort failed TTS chunk with its WAV blob (flush only)."""
        await self._chunks.fail_chunk_tts(
            workflow_id,
            chunk_index,
            blob_id,
            attempts_used,
        )

    async def reset_chunks_to_pending(
        self,
        workflow_id: uuid.UUID,
        chunk_indices: list[int] | None = None,
    ) -> int:
        """Reset FAILED chunks back to PENDING so they can be re-synthesized."""
        return await self._chunks.reset_chunks_to_pending(workflow_id, chunk_indices)

    async def create_scene(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        chunk_indices: list[int],
    ) -> WorkflowScene:
        """Create a scene and link chunks to it (flush only)."""
        return await self._scenes.create_scene(workflow_id, scene_index, chunk_indices)

    async def get_or_create_scene(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        chunk_indices: list[int],
    ) -> WorkflowScene:
        """Get existing scene or create a new one (flush only)."""
        return await self._scenes.get_or_create_scene(
            workflow_id,
            scene_index,
            chunk_indices,
        )

    async def save_scene_prompt(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        image_prompt: str,
        image_negative_prompt: str,
    ) -> None:
        """Save image prompt for a scene before GPU generation (flush only)."""
        await self._scenes.save_scene_prompt(
            workflow_id,
            scene_index,
            image_prompt,
            image_negative_prompt,
        )

    async def complete_scene_image(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        blob_id: uuid.UUID,
    ) -> None:
        """Record successful image generation for a scene (flush only)."""
        await self._scenes.complete_scene_image(workflow_id, scene_index, blob_id)

    async def start_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
    ) -> None:
        """Create or re-create a step attempt and update parent workflow."""
        await self._steps.start_step(workflow_id, step_name, flush=False)
        await self._lifecycle.start_step(workflow_id, step_name, flush=False)
        await self._session.flush()

    async def complete_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        output: StepOutputSchema | None = None,
    ) -> None:
        """Mark the RUNNING WorkflowStep as COMPLETED (flush only)."""
        await self._steps.complete_step(workflow_id, step_name, output)

    async def fail_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        error: str,
    ) -> None:
        """Mark the RUNNING WorkflowStep as FAILED (flush only)."""
        await self._steps.fail_step(workflow_id, step_name, error)

    async def complete_workflow(
        self,
        workflow_id: uuid.UUID,
        result: WorkflowResultSchema,
    ) -> None:
        """Mark the Workflow as COMPLETED with result data (flush only)."""
        await self._lifecycle.complete_workflow(workflow_id, result)

    async def fail_workflow(
        self,
        workflow_id: uuid.UUID,
        error_message: str,
    ) -> None:
        """Mark the Workflow as FAILED (flush only)."""
        await self._lifecycle.fail_workflow(workflow_id, error_message)


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


async def fork_workflow(
    session: AsyncSession,
    source_id: uuid.UUID,
    from_step: StepName,
) -> Workflow:
    """Create a new workflow forked from *source_id* starting at *from_step*."""
    return await WorkflowForkService(session).fork_workflow(source_id, from_step)


__all__ = [
    "ChunkForImageStep",
    "WorkflowChunkService",
    "WorkflowForkService",
    "WorkflowLifecycleService",
    "WorkflowReadRepository",
    "WorkflowSceneService",
    "WorkflowService",
    "WorkflowStepService",
    "fork_workflow",
    "get_chunks_for_image_step",
    "get_optional_workflow_id",
    "get_scenes_for_workflow",
]

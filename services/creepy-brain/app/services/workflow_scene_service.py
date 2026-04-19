"""Workflow scene CRUD operations."""

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
WorkflowScene: Any = _workflow_models.WorkflowScene


class WorkflowSceneService:
    """Database operations for workflow image scenes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_scene(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        chunk_indices: list[int],
    ) -> WorkflowScene:
        """Create a scene and link chunks to it (flush only)."""
        scene = WorkflowScene(
            workflow_id=workflow_id,
            scene_index=scene_index,
            image_status=ChunkStatus.PENDING,
        )
        self._session.add(scene)
        await self._session.flush()
        await self._session.refresh(scene)

        for chunk_idx in chunk_indices:
            chunk = await self._get_chunk_or_raise(workflow_id, chunk_idx)
            chunk.scene_id = scene.id
        await self._session.flush()

        return scene

    async def get_or_create_scene(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        chunk_indices: list[int],
    ) -> WorkflowScene:
        """Get existing scene or create a new one (flush only)."""
        result = await self._session.execute(
            select(WorkflowScene).where(
                WorkflowScene.workflow_id == workflow_id,
                WorkflowScene.scene_index == scene_index,
            )
        )
        scene = result.scalar_one_or_none()
        if scene is not None:
            return scene
        return await self.create_scene(workflow_id, scene_index, chunk_indices)

    async def save_scene_prompt(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        image_prompt: str,
        image_negative_prompt: str,
    ) -> None:
        """Save image prompt for a scene before GPU generation (flush only)."""
        scene = await self._get_scene_or_raise(workflow_id, scene_index)
        scene.image_prompt = image_prompt
        scene.image_negative_prompt = image_negative_prompt
        await self._session.flush()

    async def complete_scene_image(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        blob_id: uuid.UUID,
    ) -> None:
        """Record successful image generation for a scene (flush only)."""
        scene = await self._get_scene_or_raise(workflow_id, scene_index)
        scene.image_status = ChunkStatus.COMPLETED
        scene.image_blob_id = blob_id
        scene.image_completed_at = datetime.now(timezone.utc)
        await self._session.flush()

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

    async def _get_scene_or_raise(
        self, workflow_id: uuid.UUID, scene_index: int
    ) -> WorkflowScene:
        result = await self._session.execute(
            select(WorkflowScene).where(
                WorkflowScene.workflow_id == workflow_id,
                WorkflowScene.scene_index == scene_index,
            )
        )
        scene = result.scalar_one_or_none()
        if scene is None:
            raise ValueError(
                f"WorkflowScene not found: workflow_id={workflow_id} scene_index={scene_index}"
            )
        return scene

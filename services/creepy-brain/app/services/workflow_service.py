"""WorkflowChunk and WorkflowScene CRUD operations.

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
from app.models.workflow import WorkflowChunk, WorkflowScene


class WorkflowService:
    """Database operations for WorkflowChunk and WorkflowScene."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -------------------------------------------------------------------------
    # Chunk operations (TTS)
    # -------------------------------------------------------------------------

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
    ) -> None:
        """Record successful TTS for a chunk (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            chunk_index: Zero-based chunk position.
            blob_id: UUID of the saved WAV blob in ``workflow_blobs``.
            duration_sec: Audio duration in seconds.
            attempts_used: Number of synthesis attempts made (1 = first try succeeded).
        """
        chunk = await self._get_chunk_or_raise(workflow_id, chunk_index)
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
        chunk = await self._get_chunk_or_raise(workflow_id, chunk_index)
        chunk.tts_status = ChunkStatus.FAILED
        chunk.tts_audio_blob_id = blob_id
        chunk.tts_completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    # -------------------------------------------------------------------------
    # Scene operations (Image)
    # -------------------------------------------------------------------------

    async def create_scene(
        self,
        workflow_id: uuid.UUID,
        scene_index: int,
        chunk_indices: list[int],
    ) -> WorkflowScene:
        """Create a scene and link chunks to it (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            scene_index: Zero-based scene position.
            chunk_indices: List of chunk indices belonging to this scene.

        Returns:
            The created WorkflowScene.
        """
        scene = WorkflowScene(
            workflow_id=workflow_id,
            scene_index=scene_index,
            image_status=ChunkStatus.PENDING,
        )
        self._session.add(scene)
        await self._session.flush()
        await self._session.refresh(scene)

        # Link chunks to this scene
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
        """Get existing scene or create a new one (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            scene_index: Zero-based scene position.
            chunk_indices: List of chunk indices belonging to this scene.

        Returns:
            The existing or newly created WorkflowScene.
        """
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
        """Save image prompt for a scene before GPU generation (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            scene_index: Zero-based scene position.
            image_prompt: The SDXL positive prompt.
            image_negative_prompt: The SDXL negative prompt.
        """
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
        """Record successful image generation for a scene (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            scene_index: Zero-based scene position.
            blob_id: UUID of the saved PNG blob in ``workflow_blobs``.
        """
        scene = await self._get_scene_or_raise(workflow_id, scene_index)
        scene.image_status = ChunkStatus.COMPLETED
        scene.image_blob_id = blob_id
        scene.image_completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

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


async def get_chunks_for_image_step(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> list[dict[str, object]]:
    """Return chunk data needed by the image_generation step.

    Args:
        session: Active SQLAlchemy async session.
        workflow_id: The workflow whose chunks to fetch.

    Returns:
        List of dicts with ``index``, ``text``, ``blob_id``, and ``scene_id`` keys.
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
            "tts_status": c.tts_status.value,
            "scene_id": str(c.scene_id) if c.scene_id else None,
        }
        for c in chunks
    ]


async def get_scenes_for_workflow(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> list[WorkflowScene]:
    """Return all scenes for a workflow ordered by scene_index.

    Args:
        session: Active SQLAlchemy async session.
        workflow_id: The workflow whose scenes to fetch.

    Returns:
        List of WorkflowScene objects.
    """
    result = await session.execute(
        select(WorkflowScene)
        .where(WorkflowScene.workflow_id == workflow_id)
        .order_by(WorkflowScene.scene_index)
    )
    return list(result.scalars().all())


def get_optional_workflow_id(workflow_run_id: str) -> Optional[uuid.UUID]:
    """Parse the Hatchet workflow run ID string to a UUID, or return None on failure."""
    try:
        return uuid.UUID(workflow_run_id)
    except ValueError:
        return None

"""WorkflowChunk and WorkflowScene CRUD operations.

Transaction ownership convention
---------------------------------
Service methods only *flush* — they never commit.  The caller is responsible
for ``await session.commit()`` after each logical unit of work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ChunkStatus, StepName, StepStatus, WorkflowStatus
from app.models.json_schemas import WorkflowResultSchema
from app.models.workflow import Workflow, WorkflowChunk, WorkflowScene, WorkflowStep


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
        mp3_blob_id: uuid.UUID | None = None,
    ) -> None:
        """Record successful TTS for a chunk (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            chunk_index: Zero-based chunk position.
            blob_id: UUID of the saved WAV blob in ``workflow_blobs``.
            duration_sec: Audio duration in seconds.
            attempts_used: Number of synthesis attempts made (1 = first try succeeded).
            mp3_blob_id: Optional UUID of the encoded MP3 blob.
        """
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

    async def reset_chunks_to_pending(
        self,
        workflow_id: uuid.UUID,
        chunk_indices: list[int] | None = None,
    ) -> int:
        """Reset FAILED chunks back to PENDING so they can be re-synthesized.

        Args:
            workflow_id: The owning workflow UUID.
            chunk_indices: Specific chunk indices to reset. If None, resets all FAILED chunks.

        Returns:
            Number of chunks reset.
        """
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
    # Step / workflow lifecycle
    # -------------------------------------------------------------------------

    async def start_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
    ) -> None:
        """Create (or re-create) a WorkflowStep in RUNNING state (flush only).

        If no step exists yet, or the latest attempt is COMPLETED/FAILED,
        a new row is created with an incremented ``attempt_number``.
        """
        result = await self._session.execute(
            select(WorkflowStep)
            .where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_name == step_name,
            )
            .order_by(desc(WorkflowStep.attempt_number))
            .limit(1)
        )
        latest = result.scalar_one_or_none()

        if latest is None or latest.status in (
            StepStatus.COMPLETED,
            StepStatus.FAILED,
        ):
            next_attempt = (latest.attempt_number + 1) if latest else 1
            step = WorkflowStep(
                workflow_id=workflow_id,
                step_name=step_name,
                status=StepStatus.RUNNING,
                attempt_number=next_attempt,
                started_at=datetime.now(timezone.utc),
            )
            self._session.add(step)
        else:
            latest.status = StepStatus.RUNNING
            latest.started_at = datetime.now(timezone.utc)

        # Update parent workflow
        wf_result = await self._session.execute(
            select(Workflow).where(Workflow.id == workflow_id)
        )
        wf = wf_result.scalar_one_or_none()
        if wf is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        wf.current_step = step_name
        if wf.status == WorkflowStatus.PENDING:
            wf.status = WorkflowStatus.RUNNING
            wf.started_at = datetime.now(timezone.utc)

        await self._session.flush()

    async def complete_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        output: StepOutputSchema | None = None,
    ) -> None:
        """Mark the RUNNING WorkflowStep as COMPLETED (flush only).

        Args:
            workflow_id: The owning workflow UUID.
            step_name: The step that completed.
            output: Optional Pydantic output model to persist in ``output_json``
                so that retry/resume paths can hydrate parent outputs correctly.
        """
        step = await self._get_running_step_or_raise(workflow_id, step_name)
        step.status = StepStatus.COMPLETED
        step.completed_at = datetime.now(timezone.utc)
        if output is not None:
            step.output_json = output
        await self._session.flush()

    async def fail_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        error: str,
    ) -> None:
        """Mark the RUNNING WorkflowStep as FAILED (flush only)."""
        step = await self._get_running_step_or_raise(workflow_id, step_name)
        step.status = StepStatus.FAILED
        step.error = error
        step.completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def complete_workflow(
        self,
        workflow_id: uuid.UUID,
        result: WorkflowResultSchema,
    ) -> None:
        """Mark the Workflow as COMPLETED with result data (flush only)."""
        wf = await self._get_workflow_or_raise(workflow_id)
        wf.status = WorkflowStatus.COMPLETED
        wf.result_json = result
        wf.completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def fail_workflow(
        self,
        workflow_id: uuid.UUID,
        error_message: str,
    ) -> None:
        """Mark the Workflow as FAILED (flush only)."""
        wf = await self._get_workflow_or_raise(workflow_id)
        wf.status = WorkflowStatus.FAILED
        wf.error = error_message
        wf.completed_at = datetime.now(timezone.utc)
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

    async def _get_running_step_or_raise(
        self, workflow_id: uuid.UUID, step_name: StepName
    ) -> WorkflowStep:
        result = await self._session.execute(
            select(WorkflowStep).where(
                WorkflowStep.workflow_id == workflow_id,
                WorkflowStep.step_name == step_name,
                WorkflowStep.status == StepStatus.RUNNING,
            )
        )
        step = result.scalar_one_or_none()
        if step is None:
            raise ValueError(
                f"No RUNNING WorkflowStep found: workflow_id={workflow_id} step_name={step_name}"
            )
        return step

    async def _get_workflow_or_raise(
        self, workflow_id: uuid.UUID
    ) -> Workflow:
        result = await self._session.execute(
            select(Workflow).where(Workflow.id == workflow_id)
        )
        wf = result.scalar_one_or_none()
        if wf is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        return wf


class ChunkForImageStep(BaseModel):
    """Chunk data needed by image_generation and stitch_final steps."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Zero-based chunk position")
    text: str = Field(description="Chunk text content")
    blob_id: str | None = Field(description="UUID of the WAV blob (None if TTS failed)")
    tts_status: ChunkStatus = Field(description="Chunk TTS status")
    scene_id: str | None = Field(description="UUID of the linked scene (None if unlinked)")
    duration_sec: float | None = Field(
        default=None, description="TTS audio duration in seconds (None if TTS not done)"
    )


async def get_chunks_for_image_step(
    session: AsyncSession,
    workflow_id: uuid.UUID,
) -> list[ChunkForImageStep]:
    """Return chunk data needed by the image_generation and stitch_final steps.

    Args:
        session: Active SQLAlchemy async session.
        workflow_id: The workflow whose chunks to fetch.

    Returns:
        List of ChunkForImageStep models ordered by chunk_index.
    """
    result = await session.execute(
        select(WorkflowChunk)
        .where(WorkflowChunk.workflow_id == workflow_id)
        .order_by(WorkflowChunk.chunk_index)
    )
    chunks = result.scalars().all()
    return [
        ChunkForImageStep(
            index=c.chunk_index,
            text=c.chunk_text,
            blob_id=str(c.tts_audio_blob_id) if c.tts_audio_blob_id else None,
            tts_status=c.tts_status,
            scene_id=str(c.scene_id) if c.scene_id else None,
            duration_sec=c.tts_duration_sec,
        )
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


def get_optional_workflow_id(workflow_run_id: str) -> uuid.UUID | None:
    """Parse the workflow run ID string to a UUID, or return None on failure."""
    try:
        return uuid.UUID(workflow_run_id)
    except ValueError:
        return None

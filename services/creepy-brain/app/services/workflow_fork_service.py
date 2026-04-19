"""Workflow fork operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

_enums: Any = import_module("app.models.enums")
_workflow_models: Any = import_module("app.models.workflow")
StepName: Any = _enums.StepName
StepStatus: Any = _enums.StepStatus
WorkflowStatus: Any = _enums.WorkflowStatus
Workflow: Any = _workflow_models.Workflow
WorkflowChunk: Any = _workflow_models.WorkflowChunk
WorkflowScene: Any = _workflow_models.WorkflowScene
WorkflowStep: Any = _workflow_models.WorkflowStep


_FORK_STEP_ORDER: list[StepName] = [
    StepName.GENERATE_STORY,
    StepName.TTS_SYNTHESIS,
    StepName.IMAGE_GENERATION,
    StepName.STITCH_FINAL,
]


class WorkflowForkService:
    """Create workflow forks from existing workflow state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fork_workflow(
        self,
        source_id: uuid.UUID,
        from_step: StepName,
    ) -> Workflow:
        """Create a new workflow forked from *source_id* starting at *from_step*."""
        src_result = await self._session.execute(
            select(Workflow).where(Workflow.id == source_id)
        )
        src = src_result.scalar_one_or_none()
        if src is None:
            raise ValueError(f"Source workflow not found: {source_id}")

        try:
            fork_idx = _FORK_STEP_ORDER.index(from_step)
        except ValueError:
            valid = [s.value for s in _FORK_STEP_ORDER]
            raise ValueError(f"Invalid from_step '{from_step.value}'. Valid: {valid}")

        predecessor_names = _FORK_STEP_ORDER[:fork_idx]

        src_steps_result = await self._session.execute(
            select(WorkflowStep)
            .where(
                WorkflowStep.workflow_id == source_id,
                WorkflowStep.status == StepStatus.COMPLETED,
            )
            .order_by(desc(WorkflowStep.attempt_number))
        )
        src_steps_all = src_steps_result.scalars().all()
        latest_completed: dict[StepName, WorkflowStep] = {}
        for ws in src_steps_all:
            if ws.step_name not in latest_completed:
                latest_completed[ws.step_name] = ws

        new_id = uuid.uuid4()
        new_wf = Workflow(
            id=new_id,
            workflow_type=src.workflow_type,
            input_json=src.input_json,
            status=WorkflowStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._session.add(new_wf)
        await self._session.flush()

        now = datetime.now(timezone.utc)
        for step_name in predecessor_names:
            src_ws = latest_completed.get(step_name)
            seeded = WorkflowStep(
                workflow_id=new_id,
                step_name=step_name,
                status=StepStatus.COMPLETED,
                output_json=src_ws.output_json if src_ws else None,
                attempt_number=1,
                started_at=now,
                completed_at=now,
            )
            self._session.add(seeded)

        await self._session.flush()

        needs_chunks = fork_idx >= _FORK_STEP_ORDER.index(StepName.IMAGE_GENERATION)
        needs_scenes = fork_idx >= _FORK_STEP_ORDER.index(StepName.STITCH_FINAL)

        if needs_chunks:
            chunks_result = await self._session.execute(
                select(WorkflowChunk)
                .where(WorkflowChunk.workflow_id == source_id)
                .order_by(WorkflowChunk.chunk_index)
            )
            src_chunks = chunks_result.scalars().all()

            old_to_new_scene: dict[uuid.UUID, uuid.UUID] = {}

            if needs_scenes:
                scenes_result = await self._session.execute(
                    select(WorkflowScene)
                    .where(WorkflowScene.workflow_id == source_id)
                    .order_by(WorkflowScene.scene_index)
                )
                src_scenes = scenes_result.scalars().all()

                for sc in src_scenes:
                    new_scene = WorkflowScene(
                        workflow_id=new_id,
                        scene_index=sc.scene_index,
                        image_prompt=sc.image_prompt,
                        image_negative_prompt=sc.image_negative_prompt,
                        image_status=sc.image_status,
                        image_blob_id=sc.image_blob_id,
                        image_completed_at=sc.image_completed_at,
                    )
                    self._session.add(new_scene)
                    await self._session.flush()
                    await self._session.refresh(new_scene)
                    old_to_new_scene[sc.id] = new_scene.id

            for ch in src_chunks:
                new_scene_id: uuid.UUID | None = None
                if ch.scene_id is not None and ch.scene_id in old_to_new_scene:
                    new_scene_id = old_to_new_scene[ch.scene_id]
                new_chunk = WorkflowChunk(
                    workflow_id=new_id,
                    chunk_index=ch.chunk_index,
                    chunk_text=ch.chunk_text,
                    tts_status=ch.tts_status,
                    tts_audio_blob_id=ch.tts_audio_blob_id,
                    tts_mp3_blob_id=ch.tts_mp3_blob_id,
                    tts_duration_sec=ch.tts_duration_sec,
                    tts_completed_at=ch.tts_completed_at,
                    scene_id=new_scene_id,
                )
                self._session.add(new_chunk)

            await self._session.flush()

        return new_wf


async def fork_workflow(
    session: AsyncSession,
    source_id: uuid.UUID,
    from_step: StepName,
) -> Workflow:
    """Create a new workflow forked from *source_id* starting at *from_step*."""
    return await WorkflowForkService(session).fork_workflow(source_id, from_step)

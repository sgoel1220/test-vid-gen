"""Parent workflow lifecycle operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_enums: Any = import_module("app.models.enums")
_json_schemas: Any = import_module("app.models.json_schemas")
_workflow_models: Any = import_module("app.models.workflow")
StepName: Any = _enums.StepName
StepStatus: Any = _enums.StepStatus
WorkflowStatus: Any = _enums.WorkflowStatus
WorkflowType: Any = _enums.WorkflowType
WorkflowResultSchema: Any = _json_schemas.WorkflowResultSchema
Workflow: Any = _workflow_models.Workflow
WorkflowStep: Any = _workflow_models.WorkflowStep

PIPELINE_STEP_ORDER: list[StepName] = [
    StepName.GENERATE_STORY,
    StepName.TTS_SYNTHESIS,
    StepName.IMAGE_GENERATION,
    StepName.STITCH_FINAL,
]


async def find_resume_step(workflow_id: uuid.UUID, db: AsyncSession) -> StepName:
    """Return the first incomplete pipeline step.

    Raises:
        ValueError: If all steps are already completed.
    """
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
    )
    steps = result.scalars().all()
    done_statuses = {StepStatus.COMPLETED, StepStatus.SKIPPED}
    completed: set[StepName] = {
        s.step_name for s in steps if s.status in done_statuses
    }
    for step_name in PIPELINE_STEP_ORDER:
        if step_name not in completed:
            return step_name
    raise ValueError("All steps already completed — nothing to resume")


async def create_and_trigger(
    input_data: Any,
    db: AsyncSession,
    engine: Any,
) -> Workflow:
    """Create a ContentPipeline DB record and trigger it via the engine."""
    import structlog as _structlog

    _log = _structlog.get_logger()
    workflow_id = uuid.uuid4()
    workflow = Workflow(
        id=workflow_id,
        workflow_type=WorkflowType.CONTENT_PIPELINE,
        input_json=input_data,
        status=WorkflowStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(workflow)
    try:
        await db.commit()
    except Exception:
        _log.exception(
            "Failed to persist Workflow record — aborting trigger",
            workflow_id=str(workflow_id),
        )
        raise

    try:
        await engine.trigger("ContentPipeline", input_data, workflow_id)
    except Exception:
        workflow.status = WorkflowStatus.FAILED
        workflow.completed_at = datetime.now(timezone.utc)
        await db.commit()
        _log.exception("engine.trigger failed for workflow", workflow_id=str(workflow_id))
        raise

    await db.refresh(workflow)
    return workflow


class WorkflowLifecycleService:
    """Database operations for parent workflow lifecycle state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resume_workflow(self, workflow_id: uuid.UUID, engine: Any) -> None:
        """Resume a paused or failed workflow — hot-restart or cold-start."""
        if engine.has_runner(str(workflow_id)):
            step = await find_resume_step(workflow_id, self._session)
            await engine.retry_step(str(workflow_id), step.value)
        else:
            await engine.resume_from_db(workflow_id)

    async def start_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        *,
        flush: bool = True,
    ) -> None:
        """Update the parent workflow for a started step."""
        wf = await self._get_workflow_or_raise(workflow_id)
        wf.current_step = step_name
        if wf.status == WorkflowStatus.PENDING:
            wf.status = WorkflowStatus.RUNNING
            wf.started_at = datetime.now(timezone.utc)

        if flush:
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

    async def _get_workflow_or_raise(self, workflow_id: uuid.UUID) -> Workflow:
        result = await self._session.execute(
            select(Workflow).where(Workflow.id == workflow_id)
        )
        wf = result.scalar_one_or_none()
        if wf is None:
            raise ValueError(f"Workflow not found: {workflow_id}")
        return wf

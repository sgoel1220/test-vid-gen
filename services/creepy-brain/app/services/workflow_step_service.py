"""Workflow step attempt operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

_enums: Any = import_module("app.models.enums")
_json_schemas: Any = import_module("app.models.json_schemas")
_workflow_models: Any = import_module("app.models.workflow")
StepName: Any = _enums.StepName
StepStatus: Any = _enums.StepStatus
StepOutputSchema: Any = _json_schemas.StepOutputSchema
WorkflowStep: Any = _workflow_models.WorkflowStep


class WorkflowStepService:
    """Database operations for individual workflow step attempts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        *,
        flush: bool = True,
    ) -> None:
        """Create or re-create a WorkflowStep in RUNNING state."""
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

        if flush:
            await self._session.flush()

    async def complete_step(
        self,
        workflow_id: uuid.UUID,
        step_name: StepName,
        output: StepOutputSchema | None = None,
    ) -> None:
        """Mark the RUNNING WorkflowStep as COMPLETED (flush only)."""
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

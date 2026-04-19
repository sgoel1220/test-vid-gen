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
WorkflowStatus: Any = _enums.WorkflowStatus
WorkflowResultSchema: Any = _json_schemas.WorkflowResultSchema
Workflow: Any = _workflow_models.Workflow


class WorkflowLifecycleService:
    """Database operations for parent workflow lifecycle state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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

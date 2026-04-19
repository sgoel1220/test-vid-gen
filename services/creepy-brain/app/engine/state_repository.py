"""Workflow and step-row persistence for the workflow engine."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from importlib import import_module
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

OptionalSessionFactory = Callable[[], AbstractAsyncContextManager[Any | None]]

if TYPE_CHECKING:
    StepName: TypeAlias = Any
    StepStatus: TypeAlias = Any
    WorkflowStatus: TypeAlias = Any
    WorkflowType: TypeAlias = Any
else:
    _enums_module = import_module("app.models.enums")
    StepName = getattr(_enums_module, "StepName")
    StepStatus = getattr(_enums_module, "StepStatus")
    WorkflowStatus = getattr(_enums_module, "WorkflowStatus")
    WorkflowType = getattr(_enums_module, "WorkflowType")


def _workflow_model() -> Any:
    return getattr(import_module("app.models.workflow"), "Workflow")


def _workflow_step_model() -> Any:
    return getattr(import_module("app.models.workflow"), "WorkflowStep")


class WorkflowRecord(BaseModel):
    """Typed subset of a workflow DB row needed by engine controllers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workflow_type: WorkflowType
    input_json: BaseModel | None = None
    status: WorkflowStatus | None = None


class WorkflowStateRepository:
    """Read and write workflow/step state."""

    def __init__(self, optional_session_factory: OptionalSessionFactory) -> None:
        self._optional_session_factory = optional_session_factory

    async def get_workflow(self, workflow_id: uuid.UUID) -> WorkflowRecord | None:
        """Load a workflow row by id."""
        Workflow = _workflow_model()
        async with self._optional_session_factory() as session:
            if session is None:
                raise RuntimeError("Database not available for workflow lookup")
            result = await session.execute(select(Workflow).where(Workflow.id == workflow_id))
            wf = result.scalar_one_or_none()

        if wf is None:
            return None

        raw_input: object = getattr(wf, "input_json", None)
        raw_status: object = getattr(wf, "status", None)
        workflow_status_type = cast(type[Any], WorkflowStatus)
        return WorkflowRecord(
            workflow_type=getattr(wf, "workflow_type"),
            input_json=raw_input if isinstance(raw_input, BaseModel) else None,
            status=raw_status if isinstance(raw_status, workflow_status_type) else None,
        )

    async def reset_steps(self, workflow_id: uuid.UUID, step_names: set[str]) -> None:
        """Set the latest WorkflowStep row for each name back to PENDING."""
        WorkflowStep = _workflow_step_model()
        async with self._optional_session_factory() as session:
            if session is None:
                return
            for name_str in step_names:
                try:
                    name_enum = StepName(name_str)
                except ValueError:
                    continue
                result = await session.execute(
                    select(WorkflowStep)
                    .where(
                        WorkflowStep.workflow_id == workflow_id,
                        WorkflowStep.step_name == name_enum,
                    )
                    .order_by(WorkflowStep.attempt_number.desc())
                    .limit(1)
                )
                step = result.scalar_one_or_none()
                if step is not None:
                    step.status = StepStatus.PENDING
                    step.error = None
                    step.completed_at = None
            await session.commit()

    async def _update_workflow(
        self,
        workflow_id: uuid.UUID,
        update_fn: Callable[[Any], None],
    ) -> None:
        """Fetch a workflow row and apply *update_fn* to it, then commit."""
        Workflow = _workflow_model()
        async with self._optional_session_factory() as session:
            if session is None:
                return
            result = await session.execute(select(Workflow).where(Workflow.id == workflow_id))
            wf = result.scalar_one_or_none()
            if wf is not None:
                update_fn(wf)
            await session.commit()

    async def set_workflow_status_running(self, workflow_id: uuid.UUID) -> None:
        """Transition workflow to RUNNING unless it is already COMPLETED."""
        def _update(wf: Any) -> None:
            if wf.status != WorkflowStatus.COMPLETED:
                wf.status = WorkflowStatus.RUNNING
        await self._update_workflow(workflow_id, _update)

    async def mark_workflow_cancelled(self, workflow_id: uuid.UUID) -> None:
        """Mark a workflow CANCELLED and set completed_at."""
        def _update(wf: Any) -> None:
            wf.status = WorkflowStatus.CANCELLED
            wf.completed_at = datetime.now(timezone.utc)
        await self._update_workflow(workflow_id, _update)

    async def set_workflow_status(
        self,
        workflow_id: uuid.UUID,
        status: WorkflowStatus,
    ) -> None:
        """Set workflow to an arbitrary status."""
        await self._update_workflow(workflow_id, lambda wf: setattr(wf, "status", status))

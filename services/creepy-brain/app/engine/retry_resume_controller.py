"""Retry and DB-resume workflow control."""

from __future__ import annotations

import logging
import uuid
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from .registry import WorkflowDefinitionRegistry, workflow_type_to_name
from .runner import get_downstream_steps
from .state_repository import WorkflowStateRepository
from .task_supervisor import (
    CancelTaskCallback,
    RunnerFactory,
    WorkflowTaskSupervisor,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    WorkflowStatus: TypeAlias = Any
else:
    WorkflowStatus = getattr(import_module("app.models.enums"), "WorkflowStatus")


class ResetStepsInDb(Protocol):
    """Callable shape for resetting persisted step rows."""

    async def __call__(self, workflow_id: uuid.UUID, step_names: set[str]) -> None:
        """Reset step rows."""


class SetWorkflowStatusRunning(Protocol):
    """Callable shape for setting a workflow RUNNING."""

    async def __call__(self, workflow_id: uuid.UUID) -> None:
        """Set workflow RUNNING."""


class SetWorkflowStatus(Protocol):
    """Callable shape for setting workflow status."""

    async def __call__(self, workflow_id: uuid.UUID, status: WorkflowStatus) -> None:
        """Set workflow status."""


class ResumeFromDb(Protocol):
    """Callable shape for facade resume method."""

    async def __call__(self, workflow_id: uuid.UUID) -> str:
        """Resume a workflow from DB."""


class WorkflowRetryResumeController:
    """Handle retry_step and resume_from_db orchestration."""

    def __init__(
        self,
        registry: WorkflowDefinitionRegistry,
        state_repository: WorkflowStateRepository,
        task_supervisor: WorkflowTaskSupervisor,
        runner_factory: RunnerFactory,
    ) -> None:
        self._registry = registry
        self._state_repository = state_repository
        self._task_supervisor = task_supervisor
        self._runner_factory = runner_factory

    async def retry_step(
        self,
        workflow_run_id: str,
        step_name: str,
        *,
        cancel_task: CancelTaskCallback,
        reset_steps_in_db: ResetStepsInDb,
        set_workflow_status_running: SetWorkflowStatusRunning,
        resume_from_db: ResumeFromDb,
    ) -> None:
        """Reset a step and all downstream steps, then resume the workflow."""
        run_id = workflow_run_id
        workflow_id = uuid.UUID(run_id)

        existing_runner = self._task_supervisor.runners.get(run_id)
        existing_outputs = (
            existing_runner.get_outputs() if existing_runner is not None else {}
        )
        wf_def = existing_runner._def if existing_runner is not None else None

        await cancel_task(run_id, mark_cancelled_in_db=False)

        if wf_def is None:
            log.info(
                "engine: no runner for %s - cold-start retry of step '%s'",
                run_id,
                step_name,
            )
            wf_row = await self._state_repository.get_workflow(workflow_id)
            if wf_row is None:
                raise RuntimeError(f"Workflow {run_id} not found in database")

            wf_name = workflow_type_to_name(wf_row.workflow_type)
            cold_wf_def = self._registry.get(wf_name)
            reset_names = get_downstream_steps(cold_wf_def.steps, step_name)
            try:
                await reset_steps_in_db(workflow_id, reset_names)
            except Exception as exc:
                log.error(
                    "engine: cold retry_step failed to reset DB steps for %s: %s",
                    run_id,
                    exc,
                )

            await resume_from_db(workflow_id)
            log.info("engine: cold-start retry of step '%s' for workflow %s", step_name, run_id)
            return

        if existing_runner is None:
            raise RuntimeError(f"Workflow {run_id} runner missing")

        reset_names = get_downstream_steps(wf_def.steps, step_name)
        for name in reset_names:
            existing_outputs.pop(name, None)

        try:
            await reset_steps_in_db(workflow_id, reset_names)
        except Exception as exc:
            log.error("engine: retry_step failed to reset DB steps for %s: %s", run_id, exc)

        try:
            await set_workflow_status_running(workflow_id)
        except Exception as exc:
            log.error("engine: retry_step failed to update workflow status for %s: %s", run_id, exc)

        new_runner = self._runner_factory(
            wf_def,
            existing_runner.workflow_input,
            workflow_id,
            existing_outputs,
        )
        self._task_supervisor.runners[run_id] = new_runner
        self._task_supervisor.schedule_runner(new_runner, run_id, f"workflow-{run_id}-retry")
        log.info("engine: retrying step '%s' for workflow %s", step_name, run_id)

    async def resume_from_db(
        self,
        workflow_id: uuid.UUID,
        *,
        set_workflow_status: SetWorkflowStatus,
    ) -> str:
        """Resume a workflow from persisted DB state."""
        run_id = str(workflow_id)

        if run_id in self._task_supervisor.tasks and not self._task_supervisor.tasks[run_id].done():
            raise RuntimeError(f"Workflow {run_id} is already running")

        wf = await self._state_repository.get_workflow(workflow_id)
        if wf is None:
            raise RuntimeError(f"Workflow {workflow_id} not found in database")
        if wf.input_json is None:
            raise RuntimeError(f"Workflow {workflow_id} has no input_json")

        wf_name = workflow_type_to_name(wf.workflow_type)
        wf_def = self._registry.get(wf_name)

        try:
            await set_workflow_status(workflow_id, WorkflowStatus.RUNNING)
        except Exception as exc:
            log.error("engine: resume failed to set RUNNING status for %s: %s", run_id, exc)

        runner = self._runner_factory(wf_def, wf.input_json, workflow_id, None)
        self._task_supervisor.runners[run_id] = runner
        self._task_supervisor.schedule_runner(runner, run_id, f"workflow-{run_id}-resume")
        log.info("engine: resumed workflow %s from DB", run_id)
        return run_id

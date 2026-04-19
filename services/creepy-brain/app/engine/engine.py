"""WorkflowEngine facade for in-process workflow execution."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine
from importlib import import_module
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import BaseModel

from .db_helpers import get_optional_session_maker, optional_session
from .models import StepOutputMap, WorkflowDef
from .registry import (
    WORKFLOW_TYPE_NAMES as _WORKFLOW_TYPE_NAMES,
    WorkflowDefinitionRegistry,
    workflow_type_to_name as _workflow_type_to_name,
)
from .resource_controller import WorkflowResourceController
from .retry_resume_controller import WorkflowRetryResumeController
from .runner import WorkflowRunner
from .state_repository import WorkflowStateRepository
from .task_supervisor import WorkflowRunnerProtocol, WorkflowTaskSupervisor

if TYPE_CHECKING:
    WorkflowStatus: TypeAlias = Any
    WorkflowType: TypeAlias = Any
else:
    _enums_module = import_module("app.models.enums")
    WorkflowStatus = getattr(_enums_module, "WorkflowStatus")
    WorkflowType = getattr(_enums_module, "WorkflowType")


class WorkflowEngine:
    """Thin facade preserving the public workflow engine API."""

    def __init__(self) -> None:
        self._definition_registry = WorkflowDefinitionRegistry()
        self._state_repository = WorkflowStateRepository(lambda: optional_session())
        self._resource_controller = WorkflowResourceController(
            lambda: get_optional_session_maker()
        )
        self._task_supervisor = WorkflowTaskSupervisor(
            self._definition_registry,
            self._create_runner,
            self._create_task,
            self.pause,
        )
        self._retry_resume_controller = WorkflowRetryResumeController(
            self._definition_registry,
            self._state_repository,
            self._task_supervisor,
            self._create_runner,
        )

    @property
    def _registry(self) -> dict[str, WorkflowDef]:
        return self._definition_registry.definitions

    @_registry.setter
    def _registry(self, value: dict[str, WorkflowDef]) -> None:
        self._definition_registry.definitions = value

    @property
    def _tasks(self) -> dict[str, asyncio.Task[None]]:
        return self._task_supervisor.tasks

    @_tasks.setter
    def _tasks(self, value: dict[str, asyncio.Task[None]]) -> None:
        self._task_supervisor.tasks = value

    @property
    def _runners(self) -> dict[str, WorkflowRunnerProtocol]:
        return self._task_supervisor.runners

    @_runners.setter
    def _runners(self, value: dict[str, WorkflowRunnerProtocol]) -> None:
        self._task_supervisor.runners = value

    def _create_runner(
        self,
        workflow_def: WorkflowDef,
        workflow_input: object,
        workflow_id: uuid.UUID,
        completed_outputs: StepOutputMap | None,
    ) -> WorkflowRunnerProtocol:
        if completed_outputs is None:
            return WorkflowRunner(workflow_def, workflow_input, workflow_id)
        return WorkflowRunner(workflow_def, workflow_input, workflow_id, completed_outputs)

    def _create_task(
        self,
        coro: Coroutine[Any, Any, None],
        name: str,
    ) -> asyncio.Task[None]:
        return asyncio.create_task(coro, name=name)

    def register(self, workflow_def: WorkflowDef) -> None:
        """Register a workflow definition. Must be called before trigger()."""
        self._definition_registry.register(workflow_def)

    async def trigger(
        self,
        workflow_name: str,
        input: BaseModel,
        workflow_id: uuid.UUID,
    ) -> str:
        """Spawn an asyncio task to run the named workflow."""
        return await self._task_supervisor.trigger(workflow_name, input, workflow_id)

    async def retry_step(self, workflow_run_id: str, step_name: str) -> None:
        """Reset *step_name* and all downstream steps to PENDING, then resume."""
        await self._retry_resume_controller.retry_step(
            workflow_run_id,
            step_name,
            cancel_task=self._cancel_task,
            reset_steps_in_db=self._reset_steps_in_db,
            set_workflow_status_running=self._set_workflow_status_running,
            resume_from_db=self.resume_from_db,
        )

    async def pause(self, workflow_run_id: str) -> None:
        """Pause a running workflow."""
        await self._resource_controller.pause(
            workflow_run_id,
            cancel_task=self._cancel_task,
            terminate_gpu_pods=self._terminate_gpu_pods,
            set_workflow_status=self._set_workflow_status,
        )

    async def resume_from_db(self, workflow_id: uuid.UUID) -> str:
        """Resume a workflow from DB state."""
        return await self._retry_resume_controller.resume_from_db(
            workflow_id,
            set_workflow_status=self._set_workflow_status,
        )

    async def cancel(self, workflow_run_id: str) -> None:
        """Cancel a running workflow."""
        await self._resource_controller.cancel(
            workflow_run_id,
            cancel_task=self._cancel_task,
            terminate_gpu_pods=self._terminate_gpu_pods,
        )

    async def stop(self) -> None:
        """Cancel all running tasks."""
        await self._task_supervisor.stop(self._cancel_task)

    async def _run_and_cleanup(
        self,
        runner: WorkflowRunnerProtocol,
        run_id: str,
    ) -> None:
        await self._task_supervisor.run_and_cleanup(runner, run_id)

    async def _cancel_task(self, run_id: str, *, mark_cancelled_in_db: bool) -> None:
        await self._task_supervisor.cancel_task(
            run_id,
            mark_cancelled_in_db=mark_cancelled_in_db,
            mark_workflow_cancelled=self._mark_workflow_cancelled,
        )

    async def _reset_steps_in_db(
        self,
        workflow_id: uuid.UUID,
        step_names: set[str],
    ) -> None:
        await self._state_repository.reset_steps(workflow_id, step_names)

    async def _set_workflow_status_running(self, workflow_id: uuid.UUID) -> None:
        await self._state_repository.set_workflow_status_running(workflow_id)

    async def _mark_workflow_cancelled(self, workflow_id: uuid.UUID) -> None:
        await self._state_repository.mark_workflow_cancelled(workflow_id)

    async def _set_workflow_status(
        self,
        workflow_id: uuid.UUID,
        status: WorkflowStatus,
    ) -> None:
        await self._state_repository.set_workflow_status(workflow_id, status)

    async def _terminate_gpu_pods(self, workflow_id: uuid.UUID) -> None:
        await self._resource_controller.terminate_gpu_pods(workflow_id)


# ---------------------------------------------------------------------------
# Module-level singleton - import this in app/main.py
# ---------------------------------------------------------------------------

engine = WorkflowEngine()

__all__ = [
    "WorkflowEngine",
    "WorkflowStatus",
    "WorkflowType",
    "_WORKFLOW_TYPE_NAMES",
    "_workflow_type_to_name",
    "asyncio",
    "engine",
    "get_optional_session_maker",
    "optional_session",
    "WorkflowRunner",
]

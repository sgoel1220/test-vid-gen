"""Asyncio task scheduling and cleanup for workflow runs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

from pydantic import BaseModel

from .models import PauseAfterStep, StepOutputMap, WorkflowDef
from .registry import WorkflowDefinitionRegistry

log = logging.getLogger(__name__)


class WorkflowRunnerProtocol(Protocol):
    """Runtime surface used by engine controllers."""

    _def: WorkflowDef
    workflow_input: object

    async def run(self) -> None:
        """Run the workflow."""

    def get_outputs(self) -> StepOutputMap:
        """Return completed step outputs."""


RunnerFactory = Callable[
    [WorkflowDef, object, uuid.UUID, StepOutputMap | None],
    WorkflowRunnerProtocol,
]
CreateTask = Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
PauseWorkflow = Callable[[str], Awaitable[None]]
MarkWorkflowCancelled = Callable[[uuid.UUID], Awaitable[None]]


class CancelTaskCallback(Protocol):
    """Callable shape for task cancellation wrappers."""

    async def __call__(self, run_id: str, *, mark_cancelled_in_db: bool) -> None:
        """Cancel a tracked workflow task."""


class WorkflowTaskSupervisor:
    """Own asyncio task scheduling and cleanup for workflow runs."""

    def __init__(
        self,
        registry: WorkflowDefinitionRegistry,
        runner_factory: RunnerFactory,
        create_task: CreateTask,
        pause_workflow: PauseWorkflow,
    ) -> None:
        self._registry = registry
        self._runner_factory = runner_factory
        self._create_task = create_task
        self._pause_workflow = pause_workflow
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.runners: dict[str, WorkflowRunnerProtocol] = {}

    async def trigger(
        self,
        workflow_name: str,
        input: BaseModel,
        workflow_id: uuid.UUID,
    ) -> str:
        """Spawn an asyncio task to run the named workflow."""
        wf_def = self._registry.get(workflow_name)

        run_id = str(workflow_id)
        if run_id in self.tasks and not self.tasks[run_id].done():
            raise RuntimeError(f"Workflow {run_id} is already running")

        runner = self._runner_factory(wf_def, input, workflow_id, None)
        self.runners[run_id] = runner
        self.schedule_runner(runner, run_id, f"workflow-{run_id}")
        log.info("engine: triggered workflow '%s' run_id=%s", workflow_name, run_id)
        return run_id

    def schedule_runner(
        self,
        runner: WorkflowRunnerProtocol,
        run_id: str,
        task_name: str,
    ) -> None:
        """Schedule a runner and track its task by workflow run id."""
        task = self._create_task(self.run_and_cleanup(runner, run_id), task_name)
        self.tasks[run_id] = task

    async def stop(self, cancel_task: CancelTaskCallback) -> None:
        """Cancel all running tasks."""
        run_ids = list(self.tasks.keys())
        for run_id in run_ids:
            await cancel_task(run_id, mark_cancelled_in_db=True)
        log.info("engine: stopped (%d task(s) cancelled)", len(run_ids))

    async def run_and_cleanup(
        self,
        runner: WorkflowRunnerProtocol,
        run_id: str,
    ) -> None:
        """Run a runner and remove its task entry afterwards."""
        try:
            await runner.run()
        except PauseAfterStep as exc:
            log.info("engine: auto-pausing workflow %s after step '%s'", run_id, exc.step_name)
            await self._pause_workflow(run_id)
        except asyncio.CancelledError:
            log.info("engine: workflow %s task cancelled", run_id)
            raise
        except Exception as exc:
            log.error("engine: workflow %s unhandled error: %s", run_id, exc, exc_info=True)
        finally:
            self.tasks.pop(run_id, None)

    async def cancel_task(
        self,
        run_id: str,
        *,
        mark_cancelled_in_db: bool,
        mark_workflow_cancelled: MarkWorkflowCancelled,
    ) -> None:
        """Cancel a tracked task and optionally mark its workflow CANCELLED."""
        task = self.tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if mark_cancelled_in_db:
            try:
                wf_id = uuid.UUID(run_id)
                await mark_workflow_cancelled(wf_id)
            except (ValueError, Exception) as exc:
                log.error("engine: failed to mark workflow %s cancelled: %s", run_id, exc)

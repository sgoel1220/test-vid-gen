"""WorkflowEngine facade for in-process workflow execution."""

from __future__ import annotations

import asyncio
import logging
import time as _time
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
        self._nonstop_tasks: dict[str, asyncio.Task[None]] = {}

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
        return WorkflowRunner(workflow_def, workflow_input, workflow_id, completed_outputs)

    def _create_task(
        self,
        coro: Coroutine[Any, Any, None],
        name: str,
    ) -> asyncio.Task[None]:
        return asyncio.create_task(coro, name=name)

    def register(self, workflow_def: WorkflowDef) -> None:
        """Register a workflow definition. Must be called before trigger().

        Raises ValueError if a non-on_failure step name is missing from the
        StepName enum, surfacing the omission at import time rather than
        silently at runtime.
        """
        StepName = getattr(import_module("app.models.enums"), "StepName")
        for step in workflow_def.steps:
            if not step.is_on_failure:
                try:
                    StepName(step.name)
                except ValueError:
                    raise ValueError(
                        f"Workflow '{workflow_def.name}': step '{step.name}' is not in "
                        f"StepName enum. Add it to app/models/enums.py and create a DB "
                        f"migration, or mark is_on_failure=True to skip DB tracking."
                    )
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
            set_workflow_status=self._set_workflow_status,
            resume_from_db=self.resume_from_db,
        )

    async def pause(self, workflow_run_id: str) -> None:
        """Pause a running workflow."""
        self.cancel_nonstop(workflow_run_id)
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

    async def resume_nonstop(
        self,
        workflow_id: uuid.UUID,
        duration_sec: float,
        backoff_sec: float = 10.0,
        backoff_max_sec: float = 120.0,
    ) -> None:
        """Start an auto-retry loop that keeps resuming a failed workflow for *duration_sec*.

        The loop resumes the workflow, waits for it to finish, and if it fails again,
        waits with exponential backoff before retrying — until the time budget runs out.
        Cancel via :meth:`cancel_nonstop` (called by the existing Cancel endpoint).
        """
        wid = str(workflow_id)
        log = logging.getLogger("app.engine.nonstop")

        # Cancel any existing nonstop loop for this workflow.
        old_task = self._nonstop_tasks.pop(wid, None)
        if old_task is not None:
            old_task.cancel()

        async def _nonstop_loop() -> None:
            start = _time.monotonic()
            attempt = 0
            try:
                while True:
                    elapsed = _time.monotonic() - start
                    remaining = duration_sec - elapsed
                    if remaining <= 0:
                        log.info("nonstop %s: time budget exhausted after %d attempts", wid, attempt)
                        break

                    attempt += 1
                    log.info(
                        "nonstop %s: attempt %d (elapsed=%.0fs, remaining=%.0fs)",
                        wid, attempt, elapsed, remaining,
                    )

                    # Resume the workflow (cold-start or hot-restart).
                    try:
                        session_maker = get_optional_session_maker()
                        if session_maker is not None:
                            async with session_maker() as session:
                                from app.services.workflow_lifecycle_service import WorkflowLifecycleService
                                await WorkflowLifecycleService(session).resume_workflow(workflow_id, self)
                                await session.commit()
                    except Exception:
                        log.exception("nonstop %s: resume call failed", wid)

                    # Wait for the workflow task to finish.
                    task = self._task_supervisor.tasks.get(wid)
                    if task is not None:
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            # Re-raise if OUR task (the nonstop loop) was cancelled,
                            # not just the inner workflow task.
                            # Use cancelling() (Python 3.11+): cancelled() only returns
                            # True after the task is done, not while handling the error.
                            current = asyncio.current_task()
                            if current is not None and current.cancelling() > 0:
                                raise
                        except Exception:
                            pass  # expected on workflow failure

                    # Check if workflow reached a terminal or retryable state.
                    should_stop = False
                    try:
                        session_maker = get_optional_session_maker()
                        if session_maker is not None:
                            async with session_maker() as session:
                                from app.models.workflow import Workflow
                                from sqlalchemy import select as sa_select
                                row = (await session.execute(
                                    sa_select(Workflow.status).where(Workflow.id == workflow_id)
                                )).scalar_one_or_none()
                                if row is not None and row.value == "completed":
                                    log.info("nonstop %s: workflow completed after %d attempts", wid, attempt)
                                    should_stop = True
                                elif row is not None and row.value == "cancelled":
                                    log.info("nonstop %s: workflow cancelled, stopping loop", wid)
                                    should_stop = True
                                elif row is not None and row.value == "paused":
                                    log.info("nonstop %s: workflow paused, stopping loop", wid)
                                    should_stop = True
                                elif row is not None and row.value not in ("failed", "running"):
                                    log.info("nonstop %s: workflow status=%s, stopping loop", wid, row.value)
                                    should_stop = True
                                # "failed", "running" all mean: keep retrying.
                                # "running" can happen if _fail_workflow had a DB error.
                    except Exception:
                        log.exception("nonstop %s: status check failed, will retry", wid)

                    if should_stop:
                        break

                    # Backoff before next attempt.
                    delay = min(backoff_sec * (2 ** (attempt - 1)), backoff_max_sec)
                    remaining = duration_sec - (_time.monotonic() - start)
                    if remaining <= 0:
                        break
                    delay = min(delay, remaining)
                    log.info("nonstop %s: backing off %.1fs before next attempt", wid, delay)
                    await asyncio.sleep(delay)
            except asyncio.CancelledError:
                log.info("nonstop %s: loop cancelled after %d attempts", wid, attempt)
                raise
            except Exception:
                log.exception("nonstop %s: loop crashed after %d attempts", wid, attempt)
            finally:
                # Only remove ourselves if we are still the registered task.
                # A replacement loop may have already overwritten our entry.
                registered = self._nonstop_tasks.get(wid)
                if registered is not None and registered is asyncio.current_task():
                    self._nonstop_tasks.pop(wid, None)

        task = asyncio.create_task(_nonstop_loop(), name=f"nonstop-{wid}")
        self._nonstop_tasks[wid] = task

    def cancel_nonstop(self, workflow_run_id: str) -> bool:
        """Cancel a running nonstop loop. Returns True if one was found."""
        task = self._nonstop_tasks.pop(workflow_run_id, None)
        if task is not None:
            task.cancel()
            return True
        return False

    def has_runner(self, workflow_run_id: str) -> bool:
        """Return True if an in-memory runner exists for this workflow."""
        return self._task_supervisor.runners.get(workflow_run_id) is not None

    async def cancel(self, workflow_run_id: str) -> None:
        """Cancel a running workflow."""
        self.cancel_nonstop(workflow_run_id)
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
            set_workflow_status=self._set_workflow_status,
        )

    async def _reset_steps_in_db(
        self,
        workflow_id: uuid.UUID,
        step_names: set[str],
    ) -> None:
        await self._state_repository.reset_steps(workflow_id, step_names)

    

    

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

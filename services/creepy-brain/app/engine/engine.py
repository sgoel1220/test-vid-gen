"""WorkflowEngine: singleton that owns the asyncio task lifecycle.

Responsibilities:
- Workflow definition registry
- trigger(): spawn asyncio task for a named workflow
- retry_step(): reset target + downstream steps in DB + restart runner
- cancel(): cancel running task + mark workflow CANCELLED + terminate GPU pods
- stop(): cancel all running tasks on shutdown
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import select

from app.models.enums import GpuPodStatus, StepName, StepStatus, WorkflowStatus, WorkflowType
from app.models.gpu_pod import GpuPod
from app.models.workflow import Workflow, WorkflowStep
from app.services.workflow_service import WorkflowService

from .db_helpers import get_optional_session_maker, optional_session
from .models import PauseAfterStep, StepOutputMap, WorkflowDef
from .runner import WorkflowRunner, get_downstream_steps

log = logging.getLogger(__name__)

# Map WorkflowType enum → registered workflow definition name.
_WORKFLOW_TYPE_NAMES: dict[WorkflowType, str] = {
    WorkflowType.CONTENT_PIPELINE: "ContentPipeline",
}


def _workflow_type_to_name(wf_type: WorkflowType) -> str:
    """Convert a WorkflowType enum to a registered workflow name."""
    name = _WORKFLOW_TYPE_NAMES.get(wf_type)
    if name is None:
        raise KeyError(f"No workflow name mapping for type '{wf_type}'")
    return name


class WorkflowEngine:
    """In-process workflow engine.

    Usage::

        engine = WorkflowEngine()
        engine.register(content_pipeline_def)
        await engine.trigger("ContentPipeline", input_model, workflow_id)
    """

    def __init__(self) -> None:
        self._registry: dict[str, WorkflowDef] = {}
        # workflow_run_id (str) -> running asyncio.Task
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # workflow_run_id (str) -> runner (preserved for retry_step input recovery)
        self._runners: dict[str, WorkflowRunner] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, workflow_def: WorkflowDef) -> None:
        """Register a workflow definition. Must be called before trigger()."""
        self._registry[workflow_def.name] = workflow_def
        log.debug("engine: registered workflow '%s'", workflow_def.name)

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------

    async def trigger(
        self,
        workflow_name: str,
        input: BaseModel,
        workflow_id: uuid.UUID,
    ) -> str:
        """Spawn an asyncio task to run the named workflow.

        The Workflow DB row must already exist (created by the caller, e.g.
        the API route, before calling trigger).

        Args:
            workflow_name: Must match a registered WorkflowDef.name.
            input: Validated workflow input.
            workflow_id: Primary key of the existing Workflow DB row.

        Returns:
            workflow_run_id as a string (same as str(workflow_id)).
        """
        if workflow_name not in self._registry:
            raise KeyError(f"No workflow registered with name '{workflow_name}'")

        run_id = str(workflow_id)
        if run_id in self._tasks and not self._tasks[run_id].done():
            raise RuntimeError(f"Workflow {run_id} is already running")

        wf_def = self._registry[workflow_name]
        runner = WorkflowRunner(wf_def, input, workflow_id)
        self._runners[run_id] = runner

        task = asyncio.create_task(
            self._run_and_cleanup(runner, run_id),
            name=f"workflow-{run_id}",
        )
        self._tasks[run_id] = task
        log.info("engine: triggered workflow '%s' run_id=%s", workflow_name, run_id)
        return run_id

    # ------------------------------------------------------------------
    # Retry step
    # ------------------------------------------------------------------

    async def retry_step(self, workflow_run_id: str, step_name: str) -> None:
        """Reset *step_name* and all downstream steps to PENDING, then resume.

        If there is an in-memory runner (hot path), cancels it and spawns a new
        runner with the surviving step outputs preserved.

        If there is no in-memory runner (cold-start after restart), loads the
        workflow definition and input from DB, resets affected step rows, and
        delegates to ``resume_from_db`` which auto-skips completed steps.

        Args:
            workflow_run_id: str(workflow_id) of the target run.
            step_name: Step to retry (e.g. StepName.TTS_SYNTHESIS.value).
        """
        run_id = workflow_run_id

        # Recover the existing runner BEFORE cancelling (preserves outputs + input).
        existing_runner = self._runners.get(run_id)
        existing_outputs: StepOutputMap = (
            existing_runner.get_outputs() if existing_runner is not None else {}
        )
        wf_def: WorkflowDef | None = (
            existing_runner._def if existing_runner is not None else None
        )

        # Cancel current task (don't mark CANCELLED — we're resuming).
        await self._cancel_task(run_id, mark_cancelled_in_db=False)

        if wf_def is None:
            # ── Cold-start path ──────────────────────────────────────────────
            # No in-memory runner (process restarted).  Load the workflow
            # definition from the registry and reset affected steps in DB,
            # then hand off to resume_from_db which skips COMPLETED steps.
            log.info(
                "engine: no runner for %s — cold-start retry of step '%s'",
                run_id, step_name,
            )
            async with optional_session() as session:
                if session is None:
                    raise RuntimeError("Database not available for cold-start retry")
                result = await session.execute(
                    select(Workflow).where(Workflow.id == uuid.UUID(run_id))
                )
                wf_row = result.scalar_one_or_none()

            if wf_row is None:
                raise RuntimeError(f"Workflow {run_id} not found in database")

            wf_name = _workflow_type_to_name(wf_row.workflow_type)
            cold_wf_def = self._registry.get(wf_name)
            if cold_wf_def is None:
                raise KeyError(f"No workflow registered with name '{wf_name}'")

            reset_names = get_downstream_steps(cold_wf_def.steps, step_name)
            try:
                await self._reset_steps_in_db(uuid.UUID(run_id), reset_names)
            except Exception as exc:
                log.error("engine: cold retry_step failed to reset DB steps for %s: %s", run_id, exc)

            await self.resume_from_db(uuid.UUID(run_id))
            log.info("engine: cold-start retry of step '%s' for workflow %s", step_name, run_id)
            return
            # ── End cold-start path ──────────────────────────────────────────

        # ── Hot path: runner was in memory ───────────────────────────────────
        # Remove reset steps from preserved outputs so the runner re-executes them.
        reset_names = get_downstream_steps(wf_def.steps, step_name)
        for name in reset_names:
            existing_outputs.pop(name, None)

        # Reset DB rows for affected steps (best-effort; log errors but don't abort).
        try:
            await self._reset_steps_in_db(uuid.UUID(run_id), reset_names)
        except Exception as exc:
            log.error("engine: retry_step failed to reset DB steps for %s: %s", run_id, exc)

        # Ensure workflow is in RUNNING state (it may be FAILED/CANCELLED/PENDING).
        try:
            await self._set_workflow_status_running(uuid.UUID(run_id))
        except Exception as exc:
            log.error("engine: retry_step failed to update workflow status for %s: %s", run_id, exc)

        input_obj = (
            existing_runner.workflow_input if existing_runner is not None else BaseModel()
        )
        new_runner = WorkflowRunner(wf_def, input_obj, uuid.UUID(run_id), existing_outputs)
        self._runners[run_id] = new_runner

        task = asyncio.create_task(
            self._run_and_cleanup(new_runner, run_id),
            name=f"workflow-{run_id}-retry",
        )
        self._tasks[run_id] = task
        log.info("engine: retrying step '%s' for workflow %s", step_name, run_id)

    # ------------------------------------------------------------------
    # Pause
    # ------------------------------------------------------------------

    async def pause(self, workflow_run_id: str) -> None:
        """Pause a running workflow: cancel task, terminate GPU pods, set PAUSED.

        Unlike cancel, the workflow can be resumed later via resume_from_db().
        """
        run_id = workflow_run_id

        # Cancel the asyncio task (don't mark CANCELLED — we want PAUSED).
        await self._cancel_task(run_id, mark_cancelled_in_db=False)

        # Terminate GPU pods to stop billing.
        try:
            await self._terminate_gpu_pods(uuid.UUID(run_id))
        except (ValueError, Exception) as exc:
            log.error("engine: pause GPU pod termination failed for %s: %s", run_id, exc)

        # Set workflow status to PAUSED.
        try:
            await self._set_workflow_status(uuid.UUID(run_id), WorkflowStatus.PAUSED)
        except Exception as exc:
            log.error("engine: pause failed to set PAUSED status for %s: %s", run_id, exc)

        log.info("engine: paused workflow %s", run_id)

    # ------------------------------------------------------------------
    # Resume from DB (cold-start)
    # ------------------------------------------------------------------

    async def resume_from_db(self, workflow_id: uuid.UUID) -> str:
        """Resume a workflow from DB state (cold-start after crash or pause).

        Loads workflow input from DB, creates a new WorkflowRunner which
        auto-skips completed steps via _load_completed_steps() in run().

        Args:
            workflow_id: Primary key of the Workflow DB row.

        Returns:
            workflow_run_id as string.

        Raises:
            RuntimeError: If workflow not found or has no input_json.
            KeyError: If workflow type not registered.
        """
        run_id = str(workflow_id)

        # Don't resume if already running.
        if run_id in self._tasks and not self._tasks[run_id].done():
            raise RuntimeError(f"Workflow {run_id} is already running")

        # Load workflow from DB.
        async with optional_session() as session:
            if session is None:
                raise RuntimeError("Database not available for resume")
            result = await session.execute(
                select(Workflow).where(Workflow.id == workflow_id)
            )
            wf = result.scalar_one_or_none()

        if wf is None:
            raise RuntimeError(f"Workflow {workflow_id} not found in database")
        if wf.input_json is None:
            raise RuntimeError(f"Workflow {workflow_id} has no input_json")

        # Look up workflow definition by type.
        wf_name = _workflow_type_to_name(wf.workflow_type)
        if wf_name not in self._registry:
            raise KeyError(f"No workflow registered with name '{wf_name}'")

        wf_def = self._registry[wf_name]
        input_obj = wf.input_json  # Already a Pydantic model (stored as JSON)

        # Set status to RUNNING.
        try:
            await self._set_workflow_status(workflow_id, WorkflowStatus.RUNNING)
        except Exception as exc:
            log.error("engine: resume failed to set RUNNING status for %s: %s", run_id, exc)

        # Create runner — _load_completed_steps() in run() auto-skips done steps.
        runner = WorkflowRunner(wf_def, input_obj, workflow_id)
        self._runners[run_id] = runner

        task = asyncio.create_task(
            self._run_and_cleanup(runner, run_id),
            name=f"workflow-{run_id}-resume",
        )
        self._tasks[run_id] = task
        log.info("engine: resumed workflow %s from DB", run_id)
        return run_id

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel(self, workflow_run_id: str) -> None:
        """Cancel a running workflow: stop the task, mark CANCELLED, terminate GPU pods."""
        await self._cancel_task(workflow_run_id, mark_cancelled_in_db=True)
        try:
            await self._terminate_gpu_pods(uuid.UUID(workflow_run_id))
        except (ValueError, Exception) as exc:
            log.error("engine: cancel GPU pod termination failed for %s: %s", workflow_run_id, exc)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Cancel all running tasks (called on FastAPI shutdown)."""
        run_ids = list(self._tasks.keys())
        for run_id in run_ids:
            await self._cancel_task(run_id, mark_cancelled_in_db=True)
        log.info("engine: stopped (%d task(s) cancelled)", len(run_ids))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _run_and_cleanup(self, runner: WorkflowRunner, run_id: str) -> None:
        """Wrapper: run the runner and clean up the task entry afterwards."""
        try:
            await runner.run()
        except PauseAfterStep as exc:
            log.info("engine: auto-pausing workflow %s after step '%s'", run_id, exc.step_name)
            await self.pause(run_id)
        except asyncio.CancelledError:
            log.info("engine: workflow %s task cancelled", run_id)
            raise
        except Exception as exc:
            log.error("engine: workflow %s unhandled error: %s", run_id, exc, exc_info=True)
        finally:
            self._tasks.pop(run_id, None)

    async def _cancel_task(self, run_id: str, *, mark_cancelled_in_db: bool) -> None:
        task = self._tasks.pop(run_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if mark_cancelled_in_db:
            try:
                wf_id = uuid.UUID(run_id)
                await self._mark_workflow_cancelled(wf_id)
            except (ValueError, Exception) as exc:
                log.error("engine: failed to mark workflow %s cancelled: %s", run_id, exc)

    async def _reset_steps_in_db(
        self, workflow_id: uuid.UUID, step_names: set[str]
    ) -> None:
        """Set the latest WorkflowStep row for each name back to PENDING."""
        async with optional_session() as session:
            if session is None:
                return
            for name_str in step_names:
                try:
                    name_enum = StepName(name_str)
                except ValueError:
                    continue  # not a tracked step
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

    async def _set_workflow_status_running(self, workflow_id: uuid.UUID) -> None:
        """Transition workflow to RUNNING unless it is already RUNNING or COMPLETED."""
        async with optional_session() as session:
            if session is None:
                return
            result = await session.execute(
                select(Workflow).where(Workflow.id == workflow_id)
            )
            wf = result.scalar_one_or_none()
            if wf is not None and wf.status != WorkflowStatus.COMPLETED:
                wf.status = WorkflowStatus.RUNNING
            await session.commit()

    async def _mark_workflow_cancelled(self, workflow_id: uuid.UUID) -> None:
        async with optional_session() as session:
            if session is None:
                return
            result = await session.execute(
                select(Workflow).where(Workflow.id == workflow_id)
            )
            wf = result.scalar_one_or_none()
            if wf is not None:
                wf.status = WorkflowStatus.CANCELLED
                wf.completed_at = datetime.now(timezone.utc)
            await session.commit()

    async def _set_workflow_status(
        self, workflow_id: uuid.UUID, status: WorkflowStatus
    ) -> None:
        """Set workflow to an arbitrary status."""
        async with optional_session() as session:
            if session is None:
                return
            result = await session.execute(
                select(Workflow).where(Workflow.id == workflow_id)
            )
            wf = result.scalar_one_or_none()
            if wf is not None:
                wf.status = status
            await session.commit()

    async def _terminate_gpu_pods(self, workflow_id: uuid.UUID) -> None:
        """Best-effort: terminate active GPU pods for this workflow."""
        session_maker = get_optional_session_maker()
        if session_maker is None:
            return
        try:
            from app.config import settings
            from app.gpu import get_provider
            from app.gpu.lifecycle import terminate_and_finalize

            async with session_maker() as session:
                result = await session.execute(
                    select(GpuPod).where(
                        GpuPod.workflow_id == workflow_id,
                        GpuPod.status.notin_(
                            [GpuPodStatus.TERMINATED, GpuPodStatus.ERROR]
                        ),
                    )
                )
                pods = list(result.scalars().all())

            if not pods:
                return

            provider = get_provider(settings.runpod_api_key)
            for pod in pods:
                try:
                    await terminate_and_finalize(
                        provider, pod.id, session_maker, reason="workflow_cancelled"
                    )
                except Exception as exc:
                    log.error("engine: failed to terminate pod %s: %s", pod.id, exc)
        except Exception as exc:
            log.error("engine: _terminate_gpu_pods error: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton — import this in app/main.py
# ---------------------------------------------------------------------------

engine = WorkflowEngine()

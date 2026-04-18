"""WorkflowRunner: executes a WorkflowDef against the DB state.

Responsibilities:
- Topological sort of StepDef DAG
- Execute steps in dependency order with asyncio.wait_for() timeouts
- Retry loop per step (up to StepDef.max_retries)
- Persist lifecycle events to Workflow / WorkflowStep DB tables
- Run on_failure steps when the workflow fails
- Provide parent_outputs to each step via StepContext

Note on on_failure steps: they receive empty parent_outputs by design —
the failure context is available only via DB queries if needed.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import uuid

from pydantic import BaseModel
from sqlalchemy import select

import app.db as _db
from app.models.enums import StepName, StepStatus
from app.models.schemas import (
    GenerateStoryStepOutput,
    ImageGenerationStepOutput,
    StepOutputSchema,
    StitchFinalStepOutput,
    TtsSynthesisStepOutput,
)
from app.models.workflow import WorkflowStep
from app.services.workflow_service import WorkflowService

from .models import EmptyStepOutput, StepDef, StepContext, StepOutputMap, WorkflowDef

log = logging.getLogger(__name__)

_STEP_OUTPUT_TYPES = (
    GenerateStoryStepOutput,
    TtsSynthesisStepOutput,
    ImageGenerationStepOutput,
    StitchFinalStepOutput,
)


def _as_step_output_schema(output: BaseModel) -> StepOutputSchema | None:
    """Return *output* as StepOutputSchema if it is a known concrete type, else None."""
    if isinstance(output, _STEP_OUTPUT_TYPES):
        return output
    return None


# ---------------------------------------------------------------------------
# DAG helpers
# ---------------------------------------------------------------------------

def _topo_sort(workflow_name: str, steps: list[StepDef]) -> list[StepDef]:
    """Kahn's algorithm — returns steps in a valid execution order.

    Raises ValueError if the graph contains a cycle or a missing parent.
    on_failure steps are excluded from the DAG (they run outside normal flow).
    """
    normal = [s for s in steps if not s.is_on_failure]
    name_to_step = {s.name: s for s in normal}
    in_degree: dict[str, int] = {s.name: 0 for s in normal}
    # Build children index for O(n) child lookup instead of O(n²).
    children: dict[str, list[str]] = {s.name: [] for s in normal}

    for step in normal:
        for parent in step.parents:
            if parent not in name_to_step:
                raise ValueError(
                    f"Step '{step.name}' in workflow '{workflow_name}' "
                    f"references unknown parent '{parent}'"
                )
            in_degree[step.name] += 1
            children[parent].append(step.name)

    queue: collections.deque[StepDef] = collections.deque(
        s for s in normal if in_degree[s.name] == 0
    )
    result: list[StepDef] = []
    while queue:
        step = queue.popleft()
        result.append(step)
        for child_name in children[step.name]:
            in_degree[child_name] -= 1
            if in_degree[child_name] == 0:
                queue.append(name_to_step[child_name])

    if len(result) != len(normal):
        raise ValueError(f"Workflow '{workflow_name}' DAG has a cycle")
    return result


def get_downstream_steps(steps: list[StepDef], step_name: str) -> set[str]:
    """Return names of all steps transitively downstream of *step_name* (inclusive)."""
    normal = [s for s in steps if not s.is_on_failure]
    downstream: set[str] = {step_name}
    changed = True
    while changed:
        changed = False
        for step in normal:
            if step.name not in downstream:
                if any(p in downstream for p in step.parents):
                    downstream.add(step.name)
                    changed = True
    return downstream


# ---------------------------------------------------------------------------
# WorkflowRunner
# ---------------------------------------------------------------------------

class WorkflowRunner:
    """Executes a workflow definition against the database.

    Args:
        workflow_def: The workflow to execute.
        workflow_input: Validated workflow input (passed to every step function).
        workflow_id: DB primary key for the Workflow row.
        completed_outputs: Pre-loaded outputs for already-completed steps
            (used when resuming after retry_step).
    """

    def __init__(
        self,
        workflow_def: WorkflowDef,
        workflow_input: object,
        workflow_id: uuid.UUID,
        completed_outputs: StepOutputMap | None = None,
    ) -> None:
        self._def = workflow_def
        self.workflow_input = workflow_input  # public so engine can recover it for retry
        self._workflow_id = workflow_id
        # Accumulates outputs as steps complete; pre-seeded for resumed runs.
        self._outputs: StepOutputMap = dict(completed_outputs or {})

    async def run(self) -> None:
        """Execute the workflow end-to-end, persisting state to DB."""
        try:
            ordered = _topo_sort(self._def.name, self._def.steps)
        except ValueError as exc:
            log.error("workflow %s: invalid DAG: %s", self._workflow_id, exc)
            await self._fail_workflow(str(exc))
            return

        # Load already-completed steps from DB (idempotent resume support).
        await self._load_completed_steps()

        failure_error: str | None = None

        for step in ordered:
            if step.name in self._outputs:
                log.debug(
                    "workflow %s: step '%s' already completed, skipping",
                    self._workflow_id, step.name,
                )
                continue

            error = await self._execute_step(step)
            if error is not None:
                failure_error = error
                break

        if failure_error is not None:
            # Run on_failure steps, then mark workflow FAILED.
            await self._run_on_failure_steps(failure_error)
            await self._fail_workflow(failure_error)
        else:
            log.info("workflow %s: all steps completed", self._workflow_id)
            # Caller (engine) marks workflow COMPLETED after result assembly.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_completed_steps(self) -> None:
        """Pre-seed _outputs for steps that are already COMPLETED in DB."""
        session_maker = _db.async_session_maker
        if session_maker is None:
            return
        async with session_maker() as session:
            result = await session.execute(
                select(WorkflowStep).where(
                    WorkflowStep.workflow_id == self._workflow_id,
                    WorkflowStep.status == StepStatus.COMPLETED,
                )
            )
            for ws in result.scalars().all():
                name = ws.step_name.value
                if name not in self._outputs:
                    self._outputs[name] = ws.output_json or EmptyStepOutput()

    async def _execute_step(self, step: StepDef) -> str | None:
        """Execute a single step with retry. Returns error string on permanent failure."""
        # Validate all declared parents have outputs before running.
        missing = [p for p in step.parents if p not in self._outputs]
        if missing:
            error = (
                f"Step '{step.name}' missing parent outputs: {missing}. "
                "This indicates a DAG execution bug."
            )
            log.error("workflow %s: %s", self._workflow_id, error)
            await self._db_fail_step(step.name, error)
            return error

        ctx = StepContext(
            workflow_run_id=str(self._workflow_id),
            parent_outputs={p: self._outputs[p] for p in step.parents},
        )

        await self._db_start_step(step.name)

        last_error: str = ""
        for attempt in range(step.max_retries + 1):
            if attempt > 0:
                log.info(
                    "workflow %s: step '%s' retry %d/%d",
                    self._workflow_id, step.name, attempt, step.max_retries,
                )
            try:
                output = await asyncio.wait_for(
                    step.fn(self.workflow_input, ctx),
                    timeout=step.timeout_sec,
                )
                if not isinstance(output, BaseModel):
                    raise TypeError(
                        f"Step '{step.name}' must return a Pydantic model, "
                        f"got {type(output).__name__}"
                    )
                self._outputs[step.name] = output
                await self._db_complete_step(step.name, output)
                log.info("workflow %s: step '%s' completed", self._workflow_id, step.name)
                return None
            except asyncio.TimeoutError:
                last_error = f"Step '{step.name}' timed out after {step.timeout_sec}s"
                log.error("workflow %s: %s", self._workflow_id, last_error)
            except asyncio.CancelledError:
                raise  # propagate cancellation
            except Exception as exc:
                last_error = f"Step '{step.name}' failed: {exc}"
                log.error("workflow %s: %s", self._workflow_id, last_error, exc_info=True)

        await self._db_fail_step(step.name, last_error)
        return last_error

    async def _run_on_failure_steps(self, trigger_error: str) -> None:
        """Execute all on_failure steps (best-effort, errors logged not raised).

        on_failure steps receive empty parent_outputs by design; they should
        query the DB directly if they need failure context.
        """
        on_failure_steps = [s for s in self._def.steps if s.is_on_failure]
        for step in on_failure_steps:
            ctx = StepContext(
                workflow_run_id=str(self._workflow_id),
                parent_outputs={},
            )
            try:
                await asyncio.wait_for(
                    step.fn(self.workflow_input, ctx),
                    timeout=step.timeout_sec,
                )
                log.info(
                    "workflow %s: on_failure step '%s' completed",
                    self._workflow_id, step.name,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(
                    "workflow %s: on_failure step '%s' raised: %s",
                    self._workflow_id, step.name, exc,
                )

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    async def _db_start_step(self, step_name: str) -> None:
        try:
            name_enum = StepName(step_name)
        except ValueError:
            return  # step not tracked in DB (e.g. on_failure steps with custom names)
        session_maker = _db.async_session_maker
        if session_maker is None:
            return
        try:
            async with session_maker() as session:
                await WorkflowService(session).start_step(self._workflow_id, name_enum)
                await session.commit()
        except Exception as exc:
            log.error("workflow %s: _db_start_step '%s' failed: %s", self._workflow_id, step_name, exc)

    async def _db_complete_step(self, step_name: str, output: BaseModel) -> None:
        try:
            name_enum = StepName(step_name)
        except ValueError:
            return
        session_maker = _db.async_session_maker
        if session_maker is None:
            return
        try:
            async with session_maker() as session:
                await WorkflowService(session).complete_step(
                    self._workflow_id,
                    name_enum,
                    output=_as_step_output_schema(output),
                )
                await session.commit()
        except Exception as exc:
            log.error("workflow %s: _db_complete_step '%s' failed: %s", self._workflow_id, step_name, exc)

    async def _db_fail_step(self, step_name: str, error: str) -> None:
        try:
            name_enum = StepName(step_name)
        except ValueError:
            return
        session_maker = _db.async_session_maker
        if session_maker is None:
            return
        try:
            async with session_maker() as session:
                await WorkflowService(session).fail_step(self._workflow_id, name_enum, error)
                await session.commit()
        except Exception as exc:
            log.error("workflow %s: _db_fail_step '%s' failed: %s", self._workflow_id, step_name, exc)

    async def _fail_workflow(self, error: str) -> None:
        session_maker = _db.async_session_maker
        if session_maker is None:
            return
        try:
            async with session_maker() as session:
                await WorkflowService(session).fail_workflow(self._workflow_id, error)
                await session.commit()
        except Exception as exc:
            log.error("workflow %s: _fail_workflow failed: %s", self._workflow_id, exc)

    def get_outputs(self) -> StepOutputMap:
        """Return a copy of completed step outputs (used by engine for retry)."""
        return dict(self._outputs)

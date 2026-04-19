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
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from pydantic import BaseModel
from sqlalchemy import func, select

import app.db as _db
from app.log_buffer import step_name_var, workflow_id_var

from .db_helpers import optional_session
from .models import EmptyStepOutput, PauseAfterStep, StepDef, StepContext, StepOutputMap, WorkflowDef


def _runtime_import(module_name: str) -> Any:
    return import_module(module_name)


if TYPE_CHECKING:
    StepOutputSchema: TypeAlias = BaseModel
    GenerateStoryStepOutput: type[BaseModel]
    ImageGenerationStepOutput: type[BaseModel]
    StitchFinalStepOutput: type[BaseModel]
    TtsSynthesisStepOutput: type[BaseModel]
    StepName: Any
    StepStatus: Any
    WorkflowService: Any
    WorkflowStep: Any

if not TYPE_CHECKING:
    _enums_module = _runtime_import("app.models.enums")
    StepName = getattr(_enums_module, "StepName")
    StepStatus = getattr(_enums_module, "StepStatus")

    _schemas_module = _runtime_import("app.models.json_schemas")
    GenerateStoryStepOutput = getattr(_schemas_module, "GenerateStoryStepOutput")
    ImageGenerationStepOutput = getattr(_schemas_module, "ImageGenerationStepOutput")
    StepOutputSchema = getattr(_schemas_module, "StepOutputSchema")
    StitchFinalStepOutput = getattr(_schemas_module, "StitchFinalStepOutput")
    TtsSynthesisStepOutput = getattr(_schemas_module, "TtsSynthesisStepOutput")

    WorkflowStep = getattr(_runtime_import("app.models.workflow"), "WorkflowStep")
    WorkflowService = getattr(
        _runtime_import("app.services.workflow_service"),
        "WorkflowService",
    )

log = logging.getLogger(__name__)

_STEP_OUTPUT_TYPES: tuple[type[BaseModel], ...] = (
    GenerateStoryStepOutput,
    TtsSynthesisStepOutput,
    ImageGenerationStepOutput,
    StitchFinalStepOutput,
)


class WorkflowDagPlanner:
    """Plans workflow DAG execution order and downstream step sets."""

    def _topo_sort(self, workflow_name: str, steps: list[StepDef]) -> list[StepDef]:
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

    def get_downstream_steps(self, steps: list[StepDef], step_name: str) -> set[str]:
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


def _topo_sort(workflow_name: str, steps: list[StepDef]) -> list[StepDef]:
    """Compatibility wrapper for WorkflowDagPlanner._topo_sort."""
    return WorkflowDagPlanner()._topo_sort(workflow_name, steps)


def get_downstream_steps(steps: list[StepDef], step_name: str) -> set[str]:
    """Compatibility wrapper for WorkflowDagPlanner.get_downstream_steps."""
    return WorkflowDagPlanner().get_downstream_steps(steps, step_name)


class WorkflowRunState:
    """Tracks completed step outputs for a workflow run."""

    def __init__(self, preloaded_outputs: StepOutputMap | None = None) -> None:
        self._outputs: StepOutputMap = dict(preloaded_outputs or {})

    def has_output(self, step_name: str) -> bool:
        return step_name in self._outputs

    def missing_outputs(self, step_names: list[str]) -> list[str]:
        return [step_name for step_name in step_names if step_name not in self._outputs]

    def parent_outputs(self, step_names: list[str]) -> StepOutputMap:
        return {step_name: self._outputs[step_name] for step_name in step_names}

    def record_output(self, step_name: str, output: BaseModel) -> None:
        self._outputs[step_name] = output

    def record_output_if_absent(self, step_name: str, output: BaseModel) -> None:
        if step_name not in self._outputs:
            self._outputs[step_name] = output

    def get_outputs(self) -> StepOutputMap:
        """Return a copy of completed step outputs."""
        return dict(self._outputs)


class CompletedStepLoader:
    """Hydrates completed step outputs from the database for resume support."""

    def __init__(self, workflow_id: uuid.UUID, run_state: WorkflowRunState) -> None:
        self._workflow_id = workflow_id
        self._run_state = run_state

    async def load_completed_steps(self) -> None:
        """Pre-seed outputs for steps already COMPLETED in the database.

        Only the latest attempt per step name is considered — stale completed
        rows from earlier attempts must not short-circuit a subsequent run.
        """
        async with optional_session() as session:
            if session is None:
                return
            # Subquery: max attempt_number per step_name for this workflow.
            latest_attempt_sq = (
                select(
                    WorkflowStep.step_name,
                    func.max(WorkflowStep.attempt_number).label("max_attempt"),
                )
                .where(WorkflowStep.workflow_id == self._workflow_id)
                .group_by(WorkflowStep.step_name)
                .subquery()
            )
            result = await session.execute(
                select(WorkflowStep).join(
                    latest_attempt_sq,
                    (WorkflowStep.step_name == latest_attempt_sq.c.step_name)
                    & (WorkflowStep.attempt_number == latest_attempt_sq.c.max_attempt),
                ).where(
                    WorkflowStep.workflow_id == self._workflow_id,
                    WorkflowStep.status == StepStatus.COMPLETED,
                )
            )
            for ws in result.scalars().all():
                name = ws.step_name.value
                output = ws.output_json if ws.output_json is not None else EmptyStepOutput()
                self._run_state.record_output_if_absent(name, output)


class StepLifecycleRepository:
    """Persists workflow and step lifecycle events to the database."""

    def __init__(self, workflow_id: uuid.UUID) -> None:
        self._workflow_id = workflow_id

    @staticmethod
    def _as_step_output_schema(output: BaseModel) -> StepOutputSchema | None:
        """Return *output* as StepOutputSchema if it is a known concrete type, else None."""
        if isinstance(output, _STEP_OUTPUT_TYPES):
            return output
        return None

    async def _db_start_step(self, step_name: str) -> None:
        try:
            name_enum = StepName(step_name)
        except ValueError:
            return  # step not tracked in DB (e.g. on_failure steps with custom names)
        try:
            async with optional_session() as session:
                if session is None:
                    return
                await WorkflowService(session).start_step(self._workflow_id, name_enum)
                await session.commit()
        except Exception as exc:
            log.error(
                "workflow %s: _db_start_step '%s' failed: %s",
                self._workflow_id,
                step_name,
                exc,
            )

    async def _db_complete_step(self, step_name: str, output: BaseModel) -> None:
        try:
            name_enum = StepName(step_name)
        except ValueError:
            return
        try:
            async with optional_session() as session:
                if session is None:
                    return
                await WorkflowService(session).complete_step(
                    self._workflow_id,
                    name_enum,
                    output=self._as_step_output_schema(output),
                )
                await session.commit()
        except Exception as exc:
            log.error(
                "workflow %s: _db_complete_step '%s' failed: %s",
                self._workflow_id,
                step_name,
                exc,
            )

    async def _db_fail_step(self, step_name: str, error: str) -> None:
        try:
            name_enum = StepName(step_name)
        except ValueError:
            return
        try:
            async with optional_session() as session:
                if session is None:
                    return
                await WorkflowService(session).fail_step(self._workflow_id, name_enum, error)
                await session.commit()
        except Exception as exc:
            log.error(
                "workflow %s: _db_fail_step '%s' failed: %s",
                self._workflow_id,
                step_name,
                exc,
            )

    async def _fail_workflow(self, error: str) -> None:
        try:
            async with optional_session() as session:
                if session is None:
                    return
                await WorkflowService(session).fail_workflow(self._workflow_id, error)
                await session.commit()
        except Exception as exc:
            log.error("workflow %s: _fail_workflow failed: %s", self._workflow_id, exc)


def _as_step_output_schema(output: BaseModel) -> StepOutputSchema | None:
    """Compatibility wrapper for StepLifecycleRepository._as_step_output_schema."""
    return StepLifecycleRepository._as_step_output_schema(output)


class StepLifecycle(Protocol):
    """Subset of lifecycle persistence needed by the step executor."""

    async def _db_start_step(self, step_name: str) -> None:
        """Persist step start."""

    async def _db_complete_step(self, step_name: str, output: BaseModel) -> None:
        """Persist step completion."""

    async def _db_fail_step(self, step_name: str, error: str) -> None:
        """Persist step failure."""


class WorkflowStepExecutor:
    """Executes one workflow step, including retry, timeout, validation, and output."""

    def __init__(
        self,
        workflow_id: uuid.UUID,
        run_state: WorkflowRunState,
        lifecycle: StepLifecycle,
    ) -> None:
        self._workflow_id = workflow_id
        self._run_state = run_state
        self._lifecycle = lifecycle

    async def execute_step(self, step: StepDef, workflow_input: object) -> str | None:
        """Execute a single step with retry. Returns error string on permanent failure."""
        # Validate all declared parents have outputs before running.
        missing = self._run_state.missing_outputs(step.parents)
        if missing:
            error = (
                f"Step '{step.name}' missing parent outputs: {missing}. "
                "This indicates a DAG execution bug."
            )
            log.error("workflow %s: %s", self._workflow_id, error)
            await self._lifecycle._db_fail_step(step.name, error)
            return error

        ctx = StepContext(
            workflow_run_id=str(self._workflow_id),
            parent_outputs=self._run_state.parent_outputs(step.parents),
        )

        await self._lifecycle._db_start_step(step.name)

        last_error: str = ""
        for attempt in range(step.max_retries + 1):
            if attempt > 0:
                log.info(
                    "workflow %s: step '%s' retry %d/%d",
                    self._workflow_id,
                    step.name,
                    attempt,
                    step.max_retries,
                )
            try:
                wid_tok = workflow_id_var.set(str(self._workflow_id))
                step_tok = step_name_var.set(step.name)
                try:
                    output = await asyncio.wait_for(
                        step.fn(workflow_input, ctx),
                        timeout=step.timeout_sec,
                    )
                finally:
                    workflow_id_var.reset(wid_tok)
                    step_name_var.reset(step_tok)
                if not isinstance(output, BaseModel):
                    raise TypeError(
                        f"Step '{step.name}' must return a Pydantic model, "
                        f"got {type(output).__name__}"
                    )
                self._run_state.record_output(step.name, output)
                await self._lifecycle._db_complete_step(step.name, output)
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

        await self._lifecycle._db_fail_step(step.name, last_error)
        return last_error


class FailureStepRunner:
    """Runs on_failure workflow steps best-effort."""

    def __init__(self, workflow_def: WorkflowDef, workflow_id: uuid.UUID) -> None:
        self._def = workflow_def
        self._workflow_id = workflow_id

    async def run_on_failure_steps(self, workflow_input: object, trigger_error: str) -> None:
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
                    step.fn(workflow_input, ctx),
                    timeout=step.timeout_sec,
                )
                log.info(
                    "workflow %s: on_failure step '%s' completed",
                    self._workflow_id,
                    step.name,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error(
                    "workflow %s: on_failure step '%s' raised: %s",
                    self._workflow_id,
                    step.name,
                    exc,
                )


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
        preloaded_outputs: StepOutputMap | None = None,
        *,
        completed_outputs: StepOutputMap | None = None,
    ) -> None:
        self._def = workflow_def
        self.workflow_input = workflow_input  # public so engine can recover it for retry
        self._workflow_id = workflow_id
        initial_outputs = completed_outputs if completed_outputs is not None else preloaded_outputs
        self._dag_planner = WorkflowDagPlanner()
        self._run_state = WorkflowRunState(initial_outputs)
        self._completed_step_loader = CompletedStepLoader(self._workflow_id, self._run_state)
        self._lifecycle_repository = StepLifecycleRepository(self._workflow_id)
        self._step_executor = WorkflowStepExecutor(
            self._workflow_id,
            self._run_state,
            self,
        )
        self._failure_step_runner = FailureStepRunner(self._def, self._workflow_id)

    async def run(self) -> None:
        """Execute the workflow end-to-end, persisting state to DB."""
        try:
            ordered = self._dag_planner._topo_sort(self._def.name, self._def.steps)
        except ValueError as exc:
            log.error("workflow %s: invalid DAG: %s", self._workflow_id, exc)
            await self._fail_workflow(str(exc))
            return

        # Load already-completed steps from DB (idempotent resume support).
        await self._load_completed_steps()

        failure_error: str | None = None

        for step in ordered:
            if self._run_state.has_output(step.name):
                log.debug(
                    "workflow %s: step '%s' already completed, skipping",
                    self._workflow_id, step.name,
                )
                continue

            error = await self._execute_step(step)
            if error is not None:
                failure_error = error
                break

            # Auto-pause after step if configured.
            if step.auto_pause_after:
                raise PauseAfterStep(step.name)

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
        """Pre-seed outputs for steps that are already COMPLETED in DB."""
        await self._completed_step_loader.load_completed_steps()

    async def _execute_step(self, step: StepDef) -> str | None:
        """Compatibility wrapper for WorkflowStepExecutor.execute_step."""
        return await self._step_executor.execute_step(step, self.workflow_input)

    async def _run_on_failure_steps(self, trigger_error: str) -> None:
        """Compatibility wrapper for FailureStepRunner.run_on_failure_steps."""
        await self._failure_step_runner.run_on_failure_steps(self.workflow_input, trigger_error)

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    async def _db_start_step(self, step_name: str) -> None:
        await self._lifecycle_repository._db_start_step(step_name)

    async def _db_complete_step(self, step_name: str, output: BaseModel) -> None:
        await self._lifecycle_repository._db_complete_step(step_name, output)

    async def _db_fail_step(self, step_name: str, error: str) -> None:
        await self._lifecycle_repository._db_fail_step(step_name, error)

    async def _fail_workflow(self, error: str) -> None:
        await self._lifecycle_repository._fail_workflow(error)

    def get_outputs(self) -> StepOutputMap:
        """Return a copy of completed step outputs (used by engine for retry)."""
        return self._run_state.get_outputs()

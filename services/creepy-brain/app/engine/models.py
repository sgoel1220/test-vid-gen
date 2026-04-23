"""In-process workflow engine data models.



"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# Any is deliberate here: each workflow can define its own validated input model.
StepFn = Callable[[Any, "StepContext"], Awaitable[BaseModel]]
StepOutputMap = dict[str, BaseModel]
OnCompleteHook = Callable[[str, StepOutputMap], Awaitable[None]]



from app.models.step_params import BaseStepParams as BaseStepParams  # noqa: F401 re-export


class SkippedStepOutput(BaseModel):
    """Common output shape for workflow steps that are intentionally skipped."""

    model_config = ConfigDict(extra="forbid")

    skipped: Literal[True] = True
    reason: str = Field(description="Reason the step did not run")


class EmptyStepOutput(BaseModel):
    """Placeholder for historical completed steps with no serialized output."""

    model_config = ConfigDict(extra="forbid")


_T = TypeVar("_T", bound=BaseModel)


class StepContext(BaseModel):
    """Runtime context passed to each step function."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workflow_run_id: str = Field(description="UUID string of the workflow run")
    parent_outputs: StepOutputMap = Field(
        default_factory=dict,
        description="Keyed by step name, contains each completed parent step output model",
    )
    step_params: BaseStepParams | None = Field(
        default=None,
        description="Per-step typed params resolved from the workflow input",
    )

    def get_parent_output(self, step_name: str, output_type: type[_T]) -> _T | None:
        """Retrieve a parent step's output, validated against `output_type`."""
        raw = self.parent_outputs.get(step_name)
        if not isinstance(raw, output_type):
            return None
        return raw


class StepDef(BaseModel):
    """Definition of a single workflow step."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Step name, must match StepName enum value for DB tracking")
    fn: StepFn = Field(description="Async callable: (input, ctx) -> Pydantic output model")
    parents: list[str] = Field(
        default_factory=list,
        description="Names of steps that must complete before this one starts",
    )
    timeout_sec: float = Field(default=900.0, description="asyncio.wait_for timeout in seconds")
    max_retries: int = Field(default=0, ge=0, description="Max retry attempts after first failure")
    is_on_failure: bool = Field(
        default=False,
        description="If True, this step runs only when the workflow fails",
    )
    auto_pause_after: bool = Field(
        default=False,
        description="If True, the workflow pauses after this step completes successfully",
    )
    params_schema: type[BaseStepParams] | None = Field(
        default=None,
        description="Pydantic model class for this step's configurable params (for schema discovery)",
    )
    params_field: str | None = Field(
        default=None,
        description="Attribute name on WorkflowInputSchema holding this step's params",
    )



class PauseAfterStep(Exception):
    """Raised by the runner when a step with auto_pause_after completes."""

    def __init__(self, step_name: str) -> None:
        self.step_name = step_name
        super().__init__(f"Auto-pause after step '{step_name}'")

class WorkflowDef(BaseModel):
    """Definition of a complete workflow with its steps."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Unique workflow name for registry lookup")
    steps: list[StepDef] = Field(description="All steps (including on_failure steps)")
    on_complete: OnCompleteHook | None = Field(
        default=None,
        description="Called with (workflow_run_id, all_outputs) after all steps succeed.",
    )

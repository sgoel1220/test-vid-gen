"""In-process workflow engine data models.



"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    retry_duration_sec: float | None = Field(
        default=None,
        description="Keep retrying the step for up to this many seconds. "
                    "Mutually exclusive with max_retries when set.",
    )
    retry_backoff_sec: float = Field(
        default=5.0,
        ge=0,
        description="Seconds to wait between retry attempts (fixed backoff).",
    )
    retry_backoff_max_sec: float = Field(
        default=60.0,
        ge=0,
        description="Maximum backoff cap when using exponential backoff.",
    )
    retry_backoff_strategy: Literal["fixed", "exponential"] = Field(
        default="fixed",
        description="Backoff strategy between retries.",
    )

    @model_validator(mode="after")
    def _check_retry_mutual_exclusion(self) -> "StepDef":
        if self.retry_duration_sec is not None and self.max_retries > 0:
            raise ValueError(
                "Cannot set both max_retries > 0 and retry_duration_sec; "
                "use one retry strategy at a time."
            )
        return self



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

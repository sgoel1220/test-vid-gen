"""In-process workflow engine data models.



"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Any is deliberate here: each workflow can define its own validated input model.
StepFn = Callable[[Any, "StepContext"], Awaitable[BaseModel]]
StepOutputMap = dict[str, BaseModel]


class SkippedStepOutput(BaseModel):
    """Common output shape for workflow steps that are intentionally skipped."""

    model_config = ConfigDict(extra="forbid")

    skipped: Literal[True] = True
    reason: str = Field(description="Reason the step did not run")


class EmptyStepOutput(BaseModel):
    """Placeholder for historical completed steps with no serialized output."""

    model_config = ConfigDict(extra="forbid")


class StepContext(BaseModel):
    """Runtime context passed to each step function."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    workflow_run_id: str = Field(description="UUID string of the workflow run")
    parent_outputs: StepOutputMap = Field(
        default_factory=dict,
        description="Keyed by step name, contains each completed parent step output model",
    )


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

"""In-process workflow engine data models.



"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Step function signature: (workflow_input, context) -> output dict
StepFn = Callable[[Any, "StepContext"], Awaitable[dict[str, object]]]


class StepContext(BaseModel):
    """Runtime context passed to each step function."""

    workflow_run_id: str = Field(description="UUID string of the workflow run")
    parent_outputs: dict[str, dict[str, object]] = Field(
        default_factory=dict,
        description="Keyed by step name, contains the output dict of each completed parent step",
    )


class StepDef(BaseModel):
    """Definition of a single workflow step."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Step name, must match StepName enum value for DB tracking")
    fn: StepFn = Field(description="Async callable: (input, ctx) -> dict")
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


class WorkflowDef(BaseModel):
    """Definition of a complete workflow with its steps."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = Field(description="Unique workflow name for registry lookup")
    steps: list[StepDef] = Field(description="All steps (including on_failure steps)")

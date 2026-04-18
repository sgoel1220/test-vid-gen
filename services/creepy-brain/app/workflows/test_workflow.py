"""Simple two-step test workflow for verifying the engine works end-to-end."""

import asyncio

from app.engine import StepContext, StepDef, WorkflowDef, engine
from app.workflows.schemas import EmptyWorkflowInput


async def _step_one(input: EmptyWorkflowInput, ctx: StepContext) -> dict[str, object]:
    """First step — simulates work and returns a value."""
    await asyncio.sleep(2)
    return StepOneOutput(message="Step one complete", value=42)


async def _step_two(input: EmptyWorkflowInput, ctx: StepContext) -> dict[str, object]:
    """Second step — reads step_one output and doubles the value."""
    result = ctx.parent_outputs.get("step_one", {})
    raw_value = result.get("value")
    if not isinstance(raw_value, int):
        raise ValueError("step_one output value must be an int")
    value: int = raw_value
    return {"message": f"Step two complete, doubled: {value * 2}"}


test_workflow_def = WorkflowDef(
    name="TestWorkflow",
    steps=[
        StepDef(name="step_one", fn=_step_one, timeout_sec=60),
        StepDef(name="step_two", fn=_step_two, parents=["step_one"], timeout_sec=60),
    ],
)

engine.register(test_workflow_def)

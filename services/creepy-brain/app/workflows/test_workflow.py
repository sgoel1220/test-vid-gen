"""Simple two-step test workflow for verifying the Hatchet setup works end-to-end."""

import asyncio
from datetime import timedelta

from hatchet_sdk import Context, EmptyModel

from . import hatchet, WORKFLOWS

test_workflow = hatchet.workflow(name="TestWorkflow")


@test_workflow.task(execution_timeout=timedelta(minutes=1))  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
async def step_one(input: EmptyModel, ctx: Context) -> dict[str, object]:
    """First step — simulates work and returns a value."""
    await asyncio.sleep(2)
    return {"message": "Step one complete", "value": 42}


@test_workflow.task(execution_timeout=timedelta(minutes=1), parents=[step_one])  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
async def step_two(input: EmptyModel, ctx: Context) -> dict[str, object]:
    """Second step — reads step_one output and doubles the value."""
    result: dict[str, object] = ctx.task_output(step_one)
    raw_value = result["value"]
    assert isinstance(raw_value, int)
    value: int = raw_value
    return {"message": f"Step two complete, doubled: {value * 2}"}


# Register this workflow with the worker
WORKFLOWS.append(test_workflow)

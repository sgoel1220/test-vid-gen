"""In-process workflow engine — public API.

Replace hatchet_sdk with this engine for running workflows inside the
FastAPI process without an external worker daemon.

Usage::

    from app.engine import engine, WorkflowDef, StepDef, StepContext, CronScheduler

    # Register a workflow
    engine.register(WorkflowDef(name="MyWorkflow", steps=[...]))

    # Trigger a run (workflow DB row must already exist)
    await engine.trigger("MyWorkflow", input_model, workflow_id)

    # Retry a step
    await engine.retry_step(str(workflow_id), "tts_synthesis")

    # Cancel a workflow
    await engine.cancel(str(workflow_id))
"""

from .engine import WorkflowEngine, engine
from .models import StepContext, StepDef, StepFn, WorkflowDef
from .runner import WorkflowRunner, get_downstream_steps
from .scheduler import CronScheduler, CronEntry

__all__ = [
    "engine",
    "WorkflowEngine",
    "WorkflowDef",
    "StepDef",
    "StepFn",
    "StepContext",
    "WorkflowRunner",
    "get_downstream_steps",
    "CronScheduler",
    "CronEntry",
]

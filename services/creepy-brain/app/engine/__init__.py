"""In-process workflow engine — public API.

Runs workflows inside the 


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
from .models import EmptyStepOutput, SkippedStepOutput, StepContext, StepDef, StepFn, WorkflowDef
from .runner import WorkflowRunner, get_downstream_steps
from .scheduler import CronScheduler, CronEntry

__all__ = [
    "engine",
    "WorkflowEngine",
    "WorkflowDef",
    "StepDef",
    "StepFn",
    "StepContext",
    "SkippedStepOutput",
    "EmptyStepOutput",
    "WorkflowRunner",
    "get_downstream_steps",
    "CronScheduler",
    "CronEntry",
]

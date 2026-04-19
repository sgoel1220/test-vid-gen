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

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    WorkflowEngine: Any
    engine: Any
    EmptyStepOutput: Any
    SkippedStepOutput: Any
    StepContext: Any
    StepDef: Any
    StepFn: Any
    WorkflowDef: Any
    WorkflowRunner: Any
    get_downstream_steps: Any
    CronScheduler: Any
    CronEntry: Any

if not TYPE_CHECKING:
    _engine_module = import_module("app.engine.engine")
    WorkflowEngine = getattr(_engine_module, "WorkflowEngine")
    engine = getattr(_engine_module, "engine")

    _models_module = import_module("app.engine.models")
    EmptyStepOutput = getattr(_models_module, "EmptyStepOutput")
    SkippedStepOutput = getattr(_models_module, "SkippedStepOutput")
    StepContext = getattr(_models_module, "StepContext")
    StepDef = getattr(_models_module, "StepDef")
    StepFn = getattr(_models_module, "StepFn")
    WorkflowDef = getattr(_models_module, "WorkflowDef")

    _runner_module = import_module("app.engine.runner")
    WorkflowRunner = getattr(_runner_module, "WorkflowRunner")
    get_downstream_steps = getattr(_runner_module, "get_downstream_steps")

    _scheduler_module = import_module("app.engine.scheduler")
    CronScheduler = getattr(_scheduler_module, "CronScheduler")
    CronEntry = getattr(_scheduler_module, "CronEntry")

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

"""Workflow definition registry."""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING, Any, TypeAlias

from .models import WorkflowDef

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    WorkflowType: TypeAlias = Any
else:
    WorkflowType = getattr(import_module("app.models.enums"), "WorkflowType")

# Map WorkflowType enum -> registered workflow definition name.
WORKFLOW_TYPE_NAMES: dict[WorkflowType, str] = {
    WorkflowType.CONTENT_PIPELINE: "ContentPipeline",
}


def workflow_type_to_name(wf_type: WorkflowType) -> str:
    """Convert a WorkflowType enum to a registered workflow name."""
    name = WORKFLOW_TYPE_NAMES.get(wf_type)
    if name is None:
        raise KeyError(f"No workflow name mapping for type '{wf_type}'")
    return name


class WorkflowDefinitionRegistry:
    """Register and look up workflow definitions by name."""

    def __init__(self) -> None:
        self.definitions: dict[str, WorkflowDef] = {}

    def register(self, workflow_def: WorkflowDef) -> None:
        """Register a workflow definition."""
        self.definitions[workflow_def.name] = workflow_def
        log.debug("engine: registered workflow '%s'", workflow_def.name)

    def get(self, workflow_name: str) -> WorkflowDef:
        """Return a registered workflow definition."""
        workflow_def = self.definitions.get(workflow_name)
        if workflow_def is None:
            raise KeyError(f"No workflow registered with name '{workflow_name}'")
        return workflow_def

    def get_optional(self, workflow_name: str) -> WorkflowDef | None:
        """Return a workflow definition if registered."""
        return self.definitions.get(workflow_name)

    def contains(self, workflow_name: str) -> bool:
        """Return whether a workflow name has been registered."""
        return workflow_name in self.definitions

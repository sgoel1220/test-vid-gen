"""Hatchet workflow engine client and workflow registration.

Requires HATCHET_CLIENT_TOKEN environment variable to be set.
This module is only imported by the worker process, not the API server.

To register a new workflow, import it here and append to WORKFLOWS:

    from .my_workflow import MyWorkflow
    WORKFLOWS.append(MyWorkflow())
"""

from hatchet_sdk import Hatchet
from hatchet_sdk.runnables.workflow import BaseWorkflow
from typing import Any

hatchet = Hatchet()

# Explicit workflow registry — pass this list to hatchet.worker(workflows=WORKFLOWS).
# Populated by importing workflow modules below.
WORKFLOWS: list[BaseWorkflow[Any]] = []

# Register workflows by importing their modules.
# Each module appends its workflow instance to WORKFLOWS on import.
from . import test_workflow as _test_workflow  # noqa: F401

from . import content_pipeline as _content_pipeline  # noqa: F401
from . import recon as _recon  # noqa: F401

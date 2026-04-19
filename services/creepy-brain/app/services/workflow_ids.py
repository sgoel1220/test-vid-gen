"""Workflow identifier helpers."""

from __future__ import annotations

import uuid


def get_optional_workflow_id(workflow_run_id: str) -> uuid.UUID | None:
    """Parse the workflow run ID string to a UUID, or return None on failure."""
    try:
        return uuid.UUID(workflow_run_id)
    except ValueError:
        return None

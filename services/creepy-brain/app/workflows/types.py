"""Shared workflow type definitions."""

from __future__ import annotations

from pydantic import BaseModel


class EmptyModel(BaseModel):
    """Empty Pydantic model for workflows that take no structured input.

    Used as the input type for cron-scheduled and no-input workflows such
    as ReconOrphanedPods and TestWorkflow.
    """

    pass

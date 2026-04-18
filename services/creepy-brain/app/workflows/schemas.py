"""Shared workflow input schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class EmptyWorkflowInput(BaseModel):
    """Input schema for workflows that do not need user-provided fields."""

    model_config = ConfigDict(extra="forbid")

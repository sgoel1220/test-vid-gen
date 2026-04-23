"""Base class for per-step configurable parameters."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BaseStepParams(BaseModel):
    """Base for per-step configurable parameters. Every step param model inherits this."""

    enabled: bool = Field(default=True, description="Whether this step should run")

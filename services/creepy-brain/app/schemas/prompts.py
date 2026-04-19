"""Generic prompt-preview schemas.

Any pipeline step that wants to expose a "build prompts" endpoint reuses
these models so the frontend / user gets a consistent structure.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PromptEntry(BaseModel):
    """A single system+user prompt pair ready to be sent to an LLM."""

    label: str = Field(..., description="Human-readable label, e.g. 'Architect' or 'Writer – Act 2'")
    system: str = Field(..., description="System prompt text")
    user: str = Field(..., description="User prompt text (may contain {placeholders} if is_template=True)")
    notes: str | None = Field(
        default=None,
        description="Optional guidance for this prompt (e.g. how to use the output in the next step)",
    )
    is_template: bool = Field(
        default=False,
        description=(
            "True when the user prompt contains unfilled {placeholders} "
            "that must be substituted with output from a prior step"
        ),
    )


class PromptPreviewResponse(BaseModel):
    """Ordered list of prompts that a pipeline stage would send to an LLM."""

    stage: str = Field(..., description="Pipeline stage name, e.g. 'story'")
    prompts: list[PromptEntry] = Field(..., description="Prompts in execution order")
    instructions: str | None = Field(
        default=None,
        description="Top-level usage instructions shown to the user before the prompt list",
    )

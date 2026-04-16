"""Type definitions for SQLAlchemy models."""

from typing import TypeAlias

from app.models.schemas import (
    StepInputSchema,
    StepOutputSchema,
    StoryOutlineSchema,
    WorkflowInputSchema,
    WorkflowResultSchema,
)

# Workflow JSONB types
WorkflowInputJson: TypeAlias = WorkflowInputSchema
WorkflowResultJson: TypeAlias = WorkflowResultSchema

# Step JSONB types - discriminated unions
StepInputJson: TypeAlias = StepInputSchema
StepOutputJson: TypeAlias = StepOutputSchema

# Story JSONB types
StoryOutlineJson: TypeAlias = StoryOutlineSchema

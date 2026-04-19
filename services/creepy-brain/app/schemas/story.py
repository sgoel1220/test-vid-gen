"""Story API schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.models.enums import StoryStatus


class GenerateStoryRequest(BaseModel):
    """Request body for starting story generation."""

    premise: str = Field(..., min_length=10, description="Story premise or idea")


class GenerateStoryResponse(BaseModel):
    """Response after initiating story generation."""

    story_id: uuid.UUID
    status: StoryStatus


class ActResponse(BaseModel):
    """Serialized act within a story."""

    act_number: int
    title: str | None
    content: str
    word_count: int | None


class StoryResponse(BaseModel):
    """Full story detail response."""

    id: uuid.UUID
    title: str | None
    premise: str
    status: StoryStatus
    word_count: int | None
    full_text: str | None
    acts: list[ActResponse]


class StoryListItem(BaseModel):
    """Story summary for list endpoints."""

    id: uuid.UUID
    title: str | None
    premise: str
    status: StoryStatus
    word_count: int | None


class UpdateStoryRequest(BaseModel):
    """Request body for updating a story's full text."""

    full_text: str = Field(..., min_length=1, description="Updated full story text")

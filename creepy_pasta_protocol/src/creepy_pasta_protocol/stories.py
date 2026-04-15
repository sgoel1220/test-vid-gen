"""Story generation wire DTOs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field

from .common import Frozen


class StoryStatus(str, Enum):
    PENDING = "pending"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"


class GenerateStoryRequest(Frozen):
    """Request to generate a new horror story."""

    premise: str = Field(min_length=10, max_length=2000)
    label: Optional[str] = None


class PatchStoryRequest(Frozen):
    """Partial update for a story in progress."""

    status: Optional[StoryStatus] = None
    error: Optional[str] = None
    bible_json: Optional[Dict[str, Any]] = Field(default=None)
    outline_json: Optional[Dict[str, Any]] = Field(default=None)
    review_score: Optional[float] = None
    review_loops: Optional[int] = None


class StoryActDTO(Frozen):
    id: str
    story_id: str
    act_number: int
    title: str
    target_word_count: int
    text: str
    word_count: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class StorySummaryDTO(Frozen):
    id: str
    premise: str
    label: Optional[str] = None
    status: StoryStatus
    review_score: Optional[float] = None
    review_loops: int = 0
    error: Optional[str] = None
    total_word_count: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class StoryDetailDTO(Frozen):
    id: str
    premise: str
    label: Optional[str] = None
    status: StoryStatus
    bible_json: Optional[Dict[str, Any]] = Field(default=None)
    outline_json: Optional[Dict[str, Any]] = Field(default=None)
    review_score: Optional[float] = None
    review_loops: int = 0
    error: Optional[str] = None
    total_word_count: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    acts: List[StoryActDTO] = Field(default_factory=list)

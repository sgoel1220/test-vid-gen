"""Run API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChunkStatus, RunStatus


class RunChunkResponse(BaseModel):
    """Serialized chunk within a run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    chunk_index: int
    chunk_text: str
    audio_blob_id: Optional[uuid.UUID] = None
    duration_sec: Optional[float] = None
    status: ChunkStatus
    created_at: datetime


class RunResponse(BaseModel):
    """Full run detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: Optional[uuid.UUID] = None
    story_id: Optional[uuid.UUID] = None
    voice_id: Optional[uuid.UUID] = None
    input_text: str
    status: RunStatus
    final_audio_blob_id: Optional[uuid.UUID] = None
    total_duration_sec: Optional[float] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    chunks: list[RunChunkResponse] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    """Request body for creating a new run."""

    input_text: str = Field(..., min_length=1)
    voice_id: Optional[uuid.UUID] = None
    story_id: Optional[uuid.UUID] = None
    workflow_id: Optional[uuid.UUID] = None


class PatchRunRequest(BaseModel):
    """Request body for patching a run."""

    status: Optional[RunStatus] = None
    final_audio_blob_id: Optional[uuid.UUID] = None
    total_duration_sec: Optional[float] = None
    completed_at: Optional[datetime] = None

"""Run API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ChunkStatus, RunStatus


class RunChunkResponse(BaseModel):
    """Serialized chunk within a run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    chunk_index: int
    chunk_text: str
    audio_blob_id: uuid.UUID | None = None
    duration_sec: float | None = None
    status: ChunkStatus
    created_at: datetime


class RunResponse(BaseModel):
    """Full run detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_id: uuid.UUID | None = None
    story_id: uuid.UUID | None = None
    voice_id: uuid.UUID | None = None
    input_text: str
    status: RunStatus
    final_audio_blob_id: uuid.UUID | None = None
    total_duration_sec: float | None = None
    completed_at: datetime | None = None
    created_at: datetime
    chunks: list[RunChunkResponse] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    """Request body for creating a new run."""

    input_text: str = Field(..., min_length=1)
    voice_id: uuid.UUID | None = None
    story_id: uuid.UUID | None = None
    workflow_id: uuid.UUID | None = None


class PatchRunRequest(BaseModel):
    """Request body for patching a run."""

    status: RunStatus | None = None
    final_audio_blob_id: uuid.UUID | None = None
    total_duration_sec: float | None = None
    completed_at: datetime | None = None

"""Pydantic schemas for the Workflow API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ChunkStatus,
    GpuPodStatus,
    GpuProvider,
    StepName,
    StepStatus,
    WorkflowStatus,
    WorkflowType,
)
from app.models.schemas import WorkflowInputSchema, WorkflowResultSchema


class CreateWorkflowRequest(BaseModel):
    """Request body for POST /api/workflows."""

    premise: str = Field(..., description="Story premise or prompt")
    voice_name: str = Field(..., description="Voice to use for TTS")
    generate_images: bool = Field(default=False)
    stitch_video: bool = Field(default=False)
    max_revisions: int = Field(default=3)
    target_word_count: int = Field(default=5000)


class WorkflowResponse(BaseModel):
    """Summary response for a workflow run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: WorkflowStatus
    workflow_type: WorkflowType
    current_step: StepName | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


class WorkflowStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_name: StepName
    status: StepStatus
    attempt_number: int
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


class WorkflowChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_index: int
    tts_status: ChunkStatus
    tts_duration_sec: float | None


class GpuPodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: GpuProvider
    status: GpuPodStatus
    created_at: datetime
    ready_at: datetime | None
    terminated_at: datetime | None
    total_cost_cents: int


class WorkflowDetailResponse(WorkflowResponse):
    input: WorkflowInputSchema
    result: WorkflowResultSchema | None
    steps: list[WorkflowStepResponse]
    chunks: list[WorkflowChunkResponse]
    gpu_pods: list[GpuPodResponse]

"""Pydantic schemas for the Workflow API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    ChunkStatus,
    GpuPodStatus,
    GpuProvider,
    StepName,
    StepStatus,
    WorkflowStatus,
    WorkflowType,
)
from app.models.json_schemas import WorkflowInputSchema, WorkflowResultSchema
from app.validation_limits import (
    MAX_REVISIONS_MAX,
    MAX_REVISIONS_MIN,
    WORKFLOW_TARGET_WORD_COUNT_MAX,
    WORKFLOW_TARGET_WORD_COUNT_MIN,
)


class CreateWorkflowRequest(WorkflowInputSchema):
    """Request body for POST /api/workflows."""


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
    chunk_text: str
    tts_status: ChunkStatus
    tts_duration_sec: float | None
    tts_audio_blob_id: uuid.UUID | None
    tts_mp3_blob_id: uuid.UUID | None
    tts_completed_at: datetime | None
    scene_id: uuid.UUID | None


class GpuPodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: GpuProvider
    status: GpuPodStatus
    created_at: datetime
    ready_at: datetime | None
    terminated_at: datetime | None
    total_cost_cents: int


class WorkflowSceneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scene_index: int
    combined_text: str
    image_status: ChunkStatus
    image_prompt: str | None
    image_negative_prompt: str | None
    image_blob_id: uuid.UUID | None


class WorkflowLogEntryResponse(BaseModel):
    """A single captured log entry for a workflow step."""

    timestamp: str
    level: str
    message: str
    step: str | None


class WorkflowDetailResponse(WorkflowResponse):
    input: WorkflowInputSchema
    result: WorkflowResultSchema | None
    steps: list[WorkflowStepResponse]
    chunks: list[WorkflowChunkResponse]
    scenes: list[WorkflowSceneResponse]
    gpu_pods: list[GpuPodResponse]

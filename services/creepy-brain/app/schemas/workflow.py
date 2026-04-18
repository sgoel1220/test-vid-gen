"""Pydantic schemas for the Workflow API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    ChunkStatus,
    GpuPodStatus,
    StepName,
    StepStatus,
    WorkflowStatus,
    WorkflowType,
)
from app.models.schemas import WorkflowInputSchema, WorkflowResultSchema


class CreateWorkflowRequest(WorkflowInputSchema):
    """Request body for POST /api/workflows."""


class WorkflowResponse(BaseModel):
    """Summary response for a workflow run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: WorkflowStatus
    workflow_type: WorkflowType
    current_step: Optional[StepName]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error: Optional[str]


class WorkflowStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_name: StepName
    status: StepStatus
    attempt_number: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error: Optional[str]


class WorkflowChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_index: int
    tts_status: ChunkStatus
    tts_duration_sec: Optional[float]


class GpuPodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    status: GpuPodStatus
    created_at: datetime
    ready_at: Optional[datetime]
    terminated_at: Optional[datetime]
    total_cost_cents: int


class WorkflowDetailResponse(WorkflowResponse):
    input: WorkflowInputSchema
    result: Optional[WorkflowResultSchema]
    steps: list[WorkflowStepResponse]
    chunks: list[WorkflowChunkResponse]
    gpu_pods: list[GpuPodResponse]

"""Workflow management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import DbSession
from app.gpu import get_provider
from app.models.enums import GpuPodStatus, WorkflowStatus, WorkflowType
from app.models.gpu_pod import GpuPod
from app.models.schemas import WorkflowInputSchema
from app.models.workflow import Workflow
from app.schemas.workflow import (
    CreateWorkflowRequest,
    GpuPodResponse,
    WorkflowChunkResponse,
    WorkflowDetailResponse,
    WorkflowResponse,
    WorkflowStepResponse,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
log = structlog.get_logger()

_ACTIVE_POD_STATUSES = [GpuPodStatus.CREATING, GpuPodStatus.RUNNING, GpuPodStatus.READY]


# ── dev-only test endpoint ────────────────────────────────────────────────────

class WorkflowRunResponse(BaseModel):
    workflow_run_id: str


@router.post("/test", response_model=WorkflowRunResponse)  # type: ignore[untyped-decorator]
async def trigger_test_workflow() -> WorkflowRunResponse:
    """Trigger the test workflow to verify the Hatchet setup works end-to-end.

    Only available when DEV_MODE=true.
    """
    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="Not found")

    from app.workflows.test_workflow import test_workflow

    run_ref = await test_workflow.aio_run_no_wait()
    return WorkflowRunResponse(workflow_run_id=run_ref.workflow_run_id)


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_response(w: Workflow) -> WorkflowResponse:
    return WorkflowResponse(
        id=w.id,
        status=w.status,
        workflow_type=w.workflow_type,
        current_step=w.current_step,
        created_at=w.created_at,
        started_at=w.started_at,
        completed_at=w.completed_at,
        error=w.error,
    )


async def _trigger_and_create(input_data: WorkflowInputSchema, db: DbSession) -> Workflow:
    """Trigger a ContentPipeline Hatchet run and create the DB record."""
    # Import lazily to avoid importing hatchet at module load time.
    from app.workflows.content_pipeline import content_pipeline  # noqa: PLC0415

    run_ref = await content_pipeline.aio_run_no_wait(input_data)
    workflow_id = uuid.UUID(run_ref.workflow_run_id)

    workflow = Workflow(
        id=workflow_id,
        workflow_type=WorkflowType.CONTENT_PIPELINE,
        input_json=input_data,
        status=WorkflowStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(workflow)
    try:
        await db.commit()
    except Exception:
        # Hatchet run is already started but DB persistence failed.
        # Log the orphan run ID so operators can identify and cancel it manually.
        log.exception(
            "Failed to persist Workflow record after Hatchet run started — orphan run",
            hatchet_run_id=str(workflow_id),
        )
        raise
    await db.refresh(workflow)
    return workflow


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=WorkflowResponse, status_code=201)  # type: ignore[untyped-decorator]
async def create_workflow(request: CreateWorkflowRequest, db: DbSession) -> WorkflowResponse:
    """Trigger a new ContentPipeline workflow run."""
    input_data = WorkflowInputSchema(
        premise=request.premise,
        voice_name=request.voice_name,
        generate_images=request.generate_images,
        stitch_video=request.stitch_video,
        max_revisions=request.max_revisions,
        target_word_count=request.target_word_count,
    )
    workflow = await _trigger_and_create(input_data, db)
    return _to_response(workflow)


@router.get("", response_model=list[WorkflowResponse])  # type: ignore[untyped-decorator]
async def list_workflows(
    db: DbSession,
    status: Optional[WorkflowStatus] = None,
    limit: int = 20,
) -> list[WorkflowResponse]:
    """List workflows, newest first, optionally filtered by status."""
    query = select(Workflow).order_by(Workflow.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Workflow.status == status)
    result = await db.execute(query)
    return [_to_response(w) for w in result.scalars().all()]


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)  # type: ignore[untyped-decorator]
async def get_workflow(workflow_id: uuid.UUID, db: DbSession) -> WorkflowDetailResponse:
    """Get detailed workflow status: steps, chunks, and GPU pods."""
    result = await db.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.steps), selectinload(Workflow.chunks))
    )
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    pods_result = await db.execute(
        select(GpuPod).where(GpuPod.workflow_id == workflow_id)
    )
    pods = pods_result.scalars().all()

    return WorkflowDetailResponse(
        id=workflow.id,
        status=workflow.status,
        workflow_type=workflow.workflow_type,
        current_step=workflow.current_step,
        created_at=workflow.created_at,
        started_at=workflow.started_at,
        completed_at=workflow.completed_at,
        error=workflow.error,
        input=workflow.input_json,
        result=workflow.result_json,
        steps=[
            WorkflowStepResponse(
                step_name=s.step_name,
                status=s.status,
                attempt_number=s.attempt_number,
                started_at=s.started_at,
                completed_at=s.completed_at,
                error=s.error,
            )
            for s in sorted(workflow.steps, key=lambda s: s.created_at)
        ],
        chunks=[
            WorkflowChunkResponse(
                chunk_index=c.chunk_index,
                tts_status=c.tts_status,
                tts_duration_sec=c.tts_duration_sec,
                image_status=c.image_status,
                image_prompt=c.image_prompt,
            )
            for c in sorted(workflow.chunks, key=lambda c: c.chunk_index)
        ],
        gpu_pods=[
            GpuPodResponse(
                id=p.id,
                provider=p.provider,
                status=p.status,
                created_at=p.created_at,
                ready_at=p.ready_at,
                terminated_at=p.terminated_at,
                total_cost_cents=p.total_cost_cents,
            )
            for p in pods
        ],
    )


@router.post("/{workflow_id}/retry", response_model=WorkflowResponse, status_code=201)  # type: ignore[untyped-decorator]
async def retry_workflow(workflow_id: uuid.UUID, db: DbSession) -> WorkflowResponse:
    """Retry a failed workflow with the same input (creates a new Hatchet run).

    Hatchet tasks have built-in retries (e.g. tts_synthesis retries=2),
    so transient failures are handled automatically. This endpoint is for
    manually retrying after all automatic retries are exhausted.
    """
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status != WorkflowStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry FAILED workflows (current status: {workflow.status})",
        )

    new_workflow = await _trigger_and_create(workflow.input_json, db)
    return _to_response(new_workflow)


@router.delete("/{workflow_id}", status_code=204)  # type: ignore[untyped-decorator]
async def cancel_workflow(workflow_id: uuid.UUID, db: DbSession) -> None:
    """Cancel a running workflow and terminate any active GPU pods."""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if workflow.status not in {WorkflowStatus.PENDING, WorkflowStatus.RUNNING}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel workflow in status: {workflow.status}",
        )

    pods_result = await db.execute(
        select(GpuPod).where(
            GpuPod.workflow_id == workflow_id,
            GpuPod.status.in_(_ACTIVE_POD_STATUSES),
        )
    )
    active_pods = pods_result.scalars().all()
    if active_pods and settings.runpod_api_key:
        provider = get_provider(settings.runpod_api_key)
        for pod in active_pods:
            await provider.terminate_pod(pod.id)

    workflow.status = WorkflowStatus.CANCELLED
    workflow.completed_at = datetime.now(timezone.utc)
    await db.commit()

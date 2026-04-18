"""Workflow management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import DbSession
from app.engine import engine
from app.models.enums import StepName, StepStatus, WorkflowStatus, WorkflowType
from app.models.json_schemas import WorkflowInputSchema
from app.models.workflow import Workflow, WorkflowStep
from app.models.gpu_pod import GpuPod
from app.schemas.workflow import (
    CreateWorkflowRequest,
    GpuPodResponse,
    WorkflowChunkResponse,
    WorkflowDetailResponse,
    WorkflowResponse,
    WorkflowStepResponse,
)
from app.services.http_errors import require_found

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
log = structlog.get_logger()


# ── dev-only test endpoint ────────────────────────────────────────────────────

class WorkflowRunResponse(BaseModel):
    workflow_run_id: str


@router.post("/test", response_model=WorkflowRunResponse)
async def trigger_test_workflow() -> WorkflowRunResponse:
    """Trigger the test workflow to verify the engine works end-to-end.

    Only available when DEV_MODE=true.
    """
    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="Not found")

    from app.workflows.types import EmptyModel

    workflow_id = uuid.uuid4()
    run_id = await engine.trigger("TestWorkflow", EmptyModel(), workflow_id)
    return WorkflowRunResponse(workflow_run_id=run_id)


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


async def _get_workflow_or_404(
    workflow_id: uuid.UUID,
    db: DbSession,
    *,
    include_details: bool = False,
) -> Workflow:
    """Fetch a workflow or raise a 404 response."""
    query = select(Workflow).where(Workflow.id == workflow_id)
    if include_details:
        query = query.options(
            selectinload(Workflow.steps),
            selectinload(Workflow.chunks),
        )
    result = await db.execute(query)
    return require_found(result.scalar_one_or_none(), "Workflow not found")


async def _trigger_and_create(input_data: WorkflowInputSchema, db: DbSession) -> Workflow:
    """Create the DB record and trigger a ContentPipeline run via the engine."""
    workflow_id = uuid.uuid4()

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
        log.exception(
            "Failed to persist Workflow record — aborting trigger",
            workflow_id=str(workflow_id),
        )
        raise

    try:
        await engine.trigger("ContentPipeline", input_data, workflow_id)
    except Exception:
        # DB record committed but engine trigger failed — mark FAILED so it isn't stuck.
        workflow.status = WorkflowStatus.FAILED
        workflow.completed_at = datetime.now(timezone.utc)
        await db.commit()
        log.exception("engine.trigger failed for workflow", workflow_id=str(workflow_id))
        raise

    await db.refresh(workflow)
    return workflow


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=WorkflowResponse, status_code=201)
async def create_workflow(request: CreateWorkflowRequest, db: DbSession) -> WorkflowResponse:
    """Trigger a new ContentPipeline workflow run."""
    workflow = await _trigger_and_create(request, db)
    return _to_response(workflow)


@router.get("", response_model=list[WorkflowResponse])
async def list_workflows(
    db: DbSession,
    status: WorkflowStatus | None = None,
    limit: int = 20,
) -> list[WorkflowResponse]:
    """List workflows, newest first, optionally filtered by status."""
    query = select(Workflow).order_by(Workflow.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Workflow.status == status)
    result = await db.execute(query)
    return [_to_response(w) for w in result.scalars().all()]


@router.get("/{workflow_id}", response_model=WorkflowDetailResponse)
async def get_workflow(workflow_id: uuid.UUID, db: DbSession) -> WorkflowDetailResponse:
    """Get detailed workflow status: steps, chunks, and GPU pods."""
    workflow = await _get_workflow_or_404(
        workflow_id,
        db,
        include_details=True,
    )

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


@router.post("/{workflow_id}/retry", response_model=WorkflowResponse, status_code=201)
async def retry_workflow(workflow_id: uuid.UUID, db: DbSession) -> WorkflowResponse:
    """Retry a failed workflow with the same input (creates a new engine run).

    The engine handles built-in retries for transient step failures. This
    endpoint is for manually retrying after all automatic retries are exhausted.
    """
    workflow = await _get_workflow_or_404(workflow_id, db)
    if workflow.status != WorkflowStatus.FAILED:
        raise HTTPException(
            status_code=400,
            detail=f"Can only retry FAILED workflows (current status: {workflow.status})",
        )

    new_workflow = await _trigger_and_create(workflow.input_json, db)
    return _to_response(new_workflow)


@router.delete("/{workflow_id}", status_code=204)
async def cancel_workflow(workflow_id: uuid.UUID, db: DbSession) -> None:
    """Cancel a running workflow and terminate any active GPU pods."""
    workflow = await _get_workflow_or_404(workflow_id, db)
    if workflow.status not in {WorkflowStatus.PENDING, WorkflowStatus.RUNNING, WorkflowStatus.PAUSED}:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel workflow in status: {workflow.status}",
        )

    # Delegate cancellation (stops task, terminates GPU pods, marks CANCELLED in DB).
    await engine.cancel(str(workflow_id))


# ── Pydantic request models ──────────────────────────────────────────────────

class RetryStepRequest(BaseModel):
    """Request body for retrying a specific step."""

    step_name: StepName


# ── Helpers ───────────────────────────────────────────────────────────────────

# Pipeline step order — used to find the first incomplete step for resume.
_PIPELINE_ORDER: list[StepName] = [
    StepName.GENERATE_STORY,
    StepName.TTS_SYNTHESIS,
    StepName.IMAGE_GENERATION,
    StepName.STITCH_FINAL,
]


async def _find_resume_step(workflow_id: uuid.UUID, db: DbSession) -> StepName:
    """Walk pipeline order and return the first non-COMPLETED step.

    Raises:
        HTTPException: If all steps are already completed.
    """
    result = await db.execute(
        select(WorkflowStep).where(WorkflowStep.workflow_id == workflow_id)
    )
    steps = result.scalars().all()
    done_statuses = {StepStatus.COMPLETED, StepStatus.SKIPPED}
    completed: set[StepName] = {
        s.step_name for s in steps if s.status in done_statuses
    }

    for step_name in _PIPELINE_ORDER:
        if step_name not in completed:
            return step_name

    raise HTTPException(
        status_code=400,
        detail="All steps already completed — nothing to resume",
    )


# ── step-level retry, pause, resume endpoints ────────────────────────────────

@router.post("/{workflow_id}/retry-step", response_model=WorkflowResponse)
async def retry_step(
    workflow_id: uuid.UUID,
    body: RetryStepRequest,
    db: DbSession,
) -> WorkflowResponse:
    """Retry a specific step (and all downstream steps).

    Only allowed when the workflow is FAILED or CANCELLED.
    """
    workflow = await _get_workflow_or_404(workflow_id, db)
    if workflow.status not in {WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Can only retry-step on FAILED or CANCELLED workflows "
                f"(current: {workflow.status})"
            ),
        )

    await engine.retry_step(str(workflow_id), body.step_name.value)
    await db.refresh(workflow)
    return _to_response(workflow)


@router.post("/{workflow_id}/pause", status_code=204)
async def pause_workflow(workflow_id: uuid.UUID, db: DbSession) -> None:
    """Pause a running workflow.

    Cancels the in-progress task and terminates GPU pods to stop billing.
    The workflow can be resumed later via POST /{id}/resume.
    """
    workflow = await _get_workflow_or_404(workflow_id, db)
    if workflow.status != WorkflowStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Can only pause RUNNING workflows (current: {workflow.status})",
        )

    await engine.pause(str(workflow_id))


@router.post("/{workflow_id}/resume", response_model=WorkflowResponse)
async def resume_workflow(workflow_id: uuid.UUID, db: DbSession) -> WorkflowResponse:
    """Resume a paused or failed workflow.

    If the engine has a runner in memory, retries from the first incomplete step.
    Otherwise, performs a cold-start resume from DB state.
    """
    workflow = await _get_workflow_or_404(workflow_id, db)
    if workflow.status not in {WorkflowStatus.PAUSED, WorkflowStatus.FAILED}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Can only resume PAUSED or FAILED workflows "
                f"(current: {workflow.status})"
            ),
        )

    # Try in-memory retry first (runner still around from this process).
    if str(workflow_id) in engine._runners:
        resume_step = await _find_resume_step(workflow_id, db)
        await engine.retry_step(str(workflow_id), resume_step.value)
    else:
        # Cold-start: load from DB.
        await engine.resume_from_db(workflow_id)

    await db.refresh(workflow)
    return _to_response(workflow)

"""Workflow management endpoints."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import DbSession
from app.engine import engine
from app.models.enums import StepName, WorkflowStatus, WorkflowType
from app.models.workflow import Workflow, WorkflowScene, WorkflowStep
from app.models.gpu_pod import GpuPod
from app.log_buffer import log_buffer
from app.models.json_schemas import MusicGenerationStepOutput, SfxGenerationStepOutput
from app.schemas.workflow import (
    CreateWorkflowRequest,
    EncodeMp3Response,
    GpuPodResponse,
    SfxClipResponse,
    WorkflowChunkResponse,
    WorkflowDetailResponse,
    WorkflowLogEntryResponse,
    WorkflowResponse,
    WorkflowSceneResponse,
    WorkflowStepResponse,
)
from app.services.http_errors import require_found
from app.services.workflow_audio_service import encode_chunks_to_mp3 as _encode_chunks
from app.services.workflow_chunk_service import retry_tts_chunks
from app.services.workflow_fork_service import fork_and_trigger
from app.services.workflow_lifecycle_service import (
    WorkflowLifecycleService,
    create_and_trigger,
)

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

    from app.workflows.schemas import EmptyWorkflowInput

    workflow_id = uuid.uuid4()
    run_id = await engine.trigger("TestWorkflow", EmptyWorkflowInput(), workflow_id)
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
            selectinload(Workflow.scenes).selectinload(WorkflowScene.chunks),
        )
    result = await db.execute(query)
    return require_found(result.scalar_one_or_none(), "Workflow not found")


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=WorkflowResponse, status_code=201)
async def create_workflow(request: CreateWorkflowRequest, db: DbSession) -> WorkflowResponse:
    """Trigger a new ContentPipeline workflow run."""
    workflow = await create_and_trigger(request, db, engine)
    return _to_response(workflow)




class StepParamSchemaEntry(BaseModel):
    """JSON Schema for a single step's configurable params."""

    step_name: str
    params_field: str
    json_schema: dict[str, object]


class PipelineSchemaResponse(BaseModel):
    """Response for GET /api/workflows/schema."""

    steps: list[StepParamSchemaEntry]


@router.get("/schema", response_model=PipelineSchemaResponse)
async def get_pipeline_schema() -> PipelineSchemaResponse:
    """Return JSON Schema for each step's configurable params."""
    workflow_def = engine._registry.get("ContentPipeline")
    if workflow_def is None:
        raise HTTPException(status_code=404, detail="ContentPipeline not registered")

    entries: list[StepParamSchemaEntry] = []
    for step in workflow_def.steps:
        if step.params_schema is not None and step.params_field is not None:
            entries.append(
                StepParamSchemaEntry(
                    step_name=step.name,
                    params_field=step.params_field,
                    json_schema=step.params_schema.model_json_schema(),
                )
            )
    return PipelineSchemaResponse(steps=entries)


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

    sorted_steps = sorted(workflow.steps, key=lambda s: s.attempt_number, reverse=True)

    # Extract music bed blob from music_generation step output
    music_bed_blob_id: str | None = None
    # Prefer result_json if available, else fall back to step output
    if workflow.result_json and workflow.result_json.music_bed_blob_id:
        music_bed_blob_id = str(workflow.result_json.music_bed_blob_id)
    else:
        music_step = next(
            (s for s in sorted_steps if s.step_name == StepName.MUSIC_GENERATION and s.output_json is not None),
            None,
        )
        if music_step is not None and isinstance(music_step.output_json, MusicGenerationStepOutput):
            music_bed_blob_id = music_step.output_json.music_bed_blob_id

    # Extract SFX clips from the latest sfx_generation step output
    sfx_clips: list[SfxClipResponse] = []
    sfx_step = next(
        (s for s in sorted_steps if s.step_name == StepName.SFX_GENERATION and s.output_json is not None),
        None,
    )
    if sfx_step is not None and isinstance(sfx_step.output_json, SfxGenerationStepOutput):
        sfx_clips = [
            SfxClipResponse(
                scene_index=clip.scene_index,
                cue_index=clip.cue_index,
                description=clip.description,
                blob_id=clip.blob_id,
                duration_sec=clip.duration_sec,
                position=clip.position,
            )
            for clip in sfx_step.output_json.clips
        ]

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
                chunk_text=c.chunk_text,
                tts_status=c.tts_status,
                tts_duration_sec=c.tts_duration_sec,
                tts_audio_blob_id=c.tts_audio_blob_id,
                tts_mp3_blob_id=c.tts_mp3_blob_id,
                tts_completed_at=c.tts_completed_at,
                scene_id=c.scene_id,
            )
            for c in sorted(workflow.chunks, key=lambda c: c.chunk_index)
        ],
        scenes=[
            WorkflowSceneResponse(
                scene_index=s.scene_index,
                combined_text=" ".join(
                    c.chunk_text
                    for c in sorted(s.chunks, key=lambda c: c.chunk_index)
                ),
                image_status=s.image_status,
                image_prompt=s.image_prompt,
                image_negative_prompt=s.image_negative_prompt,
                image_blob_id=s.image_blob_id,
            )
            for s in sorted(workflow.scenes, key=lambda s: s.scene_index)
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
        sfx_clips=sfx_clips,
        music_bed_blob_id=music_bed_blob_id,
    )


@router.get("/{workflow_id}/logs", response_model=list[WorkflowLogEntryResponse])
async def get_workflow_logs(workflow_id: uuid.UUID) -> list[WorkflowLogEntryResponse]:
    """Return in-memory log lines captured during step execution for this workflow."""
    entries = log_buffer.get(str(workflow_id))
    return [
        WorkflowLogEntryResponse(
            timestamp=e.timestamp,
            level=e.level,
            message=e.message,
            step=e.step,
        )
        for e in entries
    ]


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

    new_workflow = await create_and_trigger(workflow.input_json, db, engine)
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


class RetryChunksRequest(BaseModel):
    """Request body for retrying specific TTS chunks."""

    chunk_indices: list[int] | None = None
    """Chunk indices to retry. If omitted, all FAILED chunks are retried."""


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


@router.post("/{workflow_id}/retry-chunks", response_model=WorkflowResponse)
async def retry_chunks(
    workflow_id: uuid.UUID,
    body: RetryChunksRequest,
    db: DbSession,
) -> WorkflowResponse:
    """Reset specific FAILED TTS chunks to PENDING and retry the TTS step.

    Allowed when the workflow is FAILED, CANCELLED, or COMPLETED (completed workflows
    may still contain failed chunks when stitch succeeded on best-effort audio).
    If ``chunk_indices`` is omitted, all FAILED chunks are reset.

    Chunk resets are committed only after the retry is successfully scheduled so that
    a missing in-memory runner causes an automatic rollback — leaving chunks in their
    original FAILED state rather than a broken PENDING state with no blobs.
    """
    _RETRYABLE = {WorkflowStatus.FAILED, WorkflowStatus.CANCELLED, WorkflowStatus.COMPLETED}
    workflow = await _get_workflow_or_404(workflow_id, db)
    if workflow.status not in _RETRYABLE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Can only retry chunks on FAILED, CANCELLED, or COMPLETED workflows "
                f"(current: {workflow.status})"
            ),
        )

    reset_count = await retry_tts_chunks(workflow_id, body.chunk_indices, db, engine)

    if reset_count == 0:
        raise HTTPException(status_code=400, detail="No FAILED chunks found to retry")

    await db.commit()

    log.info(
        "retry_chunks: reset %d chunk(s) to PENDING workflow_id=%s",
        reset_count,
        workflow_id,
    )

    await db.refresh(workflow)
    return _to_response(workflow)


@router.post("/{workflow_id}/encode-mp3", response_model=EncodeMp3Response)
async def encode_chunks_to_mp3(
    workflow_id: uuid.UUID,
    db: DbSession,
) -> EncodeMp3Response:
    """Encode WAV chunk blobs to MP3 in-place, without re-running TTS.

    Finds all chunks for this workflow that have a WAV blob (tts_audio_blob_id)
    but no MP3 blob (tts_mp3_blob_id = NULL), encodes them locally using ffmpeg,
    stores the MP3 as a new blob, and writes tts_mp3_blob_id back to the chunk row.
    """
    return await _encode_chunks(workflow_id, db)


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

    await WorkflowLifecycleService(db).resume_workflow(workflow_id, engine)
    await db.refresh(workflow)
    return _to_response(workflow)


# ── Fork ──────────────────────────────────────────────────────────────────────

class ForkRequest(BaseModel):
    """Request body for forking a workflow from a specific step."""

    from_step: StepName


class ForkResponse(BaseModel):
    """Response body for fork endpoint."""

    workflow_id: str


@router.post("/{workflow_id}/fork", response_model=ForkResponse, status_code=201)
async def fork_workflow_endpoint(
    workflow_id: uuid.UUID,
    body: ForkRequest,
    db: DbSession,
) -> ForkResponse:
    """Fork a workflow: copy prior step data and re-run from *from_step* onward.

    Creates a new workflow that inherits all DB data (story, chunks, scenes) from
    the source workflow up to but not including *from_step*, then triggers fresh
    execution from that step.  The source workflow is left untouched.

    Allowed on any workflow status (useful to re-stitch a completed workflow with
    different parameters, or to re-generate images from an already-stitched run).
    """
    await _get_workflow_or_404(workflow_id, db)

    new_wf = await fork_and_trigger(db, workflow_id, body.from_step, engine)

    return ForkResponse(workflow_id=str(new_wf.id))

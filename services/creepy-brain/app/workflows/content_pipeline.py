"""ContentPipeline workflow definition.

End-to-end content pipeline: Story → TTS → Images → Stitch.

Step order and timeouts:
    generate_story     (local, 15 min, 2 retries)
    tts_synthesis      (GPU pod, 30 min)
    image_generation   (GPU pod, 45 min — covers pod readiness + N scenes × 180s/scene + LLM)
    stitch_final       (local, 5 min)
    cleanup_gpu_pod    (on_failure hook, 2 min)
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Optional

from hatchet_sdk import Context

import app.db as _db
from app.models.enums import StepName
from app.models.schemas import WorkflowInputSchema, WorkflowResultSchema
from app.services.workflow_service import WorkflowService, get_optional_workflow_id

from . import WORKFLOWS, hatchet
from .steps import cleanup, image, stitch, story, tts

log = logging.getLogger(__name__)

import asyncio

_db_init_lock = asyncio.Lock()



def _to_uuid(val: object) -> uuid.UUID | None:
    """Safely convert a string/UUID value to UUID, or return None."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, AttributeError):
        return None

async def _ensure_db() -> None:
    """Initialize the DB engine if not already done (idempotent)."""
    async with _db_init_lock:
        if _db.async_session_maker is None:
            await _db.init_db()

content_pipeline = hatchet.workflow(
    name="ContentPipeline",
    input_validator=WorkflowInputSchema,
)


async def _track_step(
    step_name: StepName,
    ctx: Context,
    result: dict[str, object],
) -> None:
    """Record step completion in the Workflow and WorkflowStep tables."""
    await _ensure_db()
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        return

    session_maker = _db.async_session_maker
    if session_maker is None:
        return

    async with session_maker() as session:
        svc = WorkflowService(session)
        await svc.complete_step(workflow_id, step_name)
        await session.commit()


async def _track_step_start(
    step_name: StepName,
    ctx: Context,
) -> None:
    """Record step start in the Workflow and WorkflowStep tables."""
    await _ensure_db()
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        return

    session_maker = _db.async_session_maker
    if session_maker is None:
        return

    async with session_maker() as session:
        svc = WorkflowService(session)
        await svc.start_step(workflow_id, step_name)
        await session.commit()


async def _track_step_failure(
    step_name: StepName,
    ctx: Context,
    error: str,
) -> None:
    """Record step failure in the Workflow and WorkflowStep tables."""
    await _ensure_db()
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        return

    session_maker = _db.async_session_maker
    if session_maker is None:
        return

    async with session_maker() as session:
        svc = WorkflowService(session)
        await svc.fail_step(workflow_id, step_name, error)
        await session.commit()


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=15),
    retries=2,
)
async def generate_story(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate story from premise using the LLM pipeline."""
    await _track_step_start(StepName.GENERATE_STORY, ctx)
    try:
        result = await story.execute(input, ctx)
    except Exception as exc:
        await _track_step_failure(StepName.GENERATE_STORY, ctx, str(exc))
        raise
    await _track_step(StepName.GENERATE_STORY, ctx, result)
    return result


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=30),
    retries=2,
    parents=[generate_story],
)
async def tts_synthesis(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Synthesize audio for the story via TTS server on a GPU pod."""
    await _track_step_start(StepName.TTS_SYNTHESIS, ctx)
    try:
        result = await tts.execute(input, ctx)
    except Exception as exc:
        await _track_step_failure(StepName.TTS_SYNTHESIS, ctx, str(exc))
        raise
    await _track_step(StepName.TTS_SYNTHESIS, ctx, result)
    return result


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=45),
    retries=2,
    parents=[tts_synthesis],
)
async def image_generation(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate images for each story chunk on a GPU pod."""
    await _track_step_start(StepName.IMAGE_GENERATION, ctx)
    try:
        result = await image.execute(input, ctx)
    except Exception as exc:
        await _track_step_failure(StepName.IMAGE_GENERATION, ctx, str(exc))
        raise
    await _track_step(StepName.IMAGE_GENERATION, ctx, result)
    return result


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=5),
    parents=[image_generation],
)
async def stitch_final(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Stitch audio and images into the final video."""
    await _track_step_start(StepName.STITCH_FINAL, ctx)
    try:
        result = await stitch.execute(input, ctx)
    except Exception as exc:
        await _track_step_failure(StepName.STITCH_FINAL, ctx, str(exc))
        raise

    # Mark step complete
    await _track_step(StepName.STITCH_FINAL, ctx, result)

    # Mark workflow complete (skip if stitch was skipped)
    if not result.get("skipped"):
        workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
        if workflow_id is not None:
            session_maker = _db.async_session_maker
            if session_maker is not None:
                async with session_maker() as session:
                    svc = WorkflowService(session)
                    wf_result = WorkflowResultSchema(
                        final_audio_blob_id=_to_uuid(result.get("final_audio_blob_id")),
                        final_video_blob_id=_to_uuid(result.get("final_video_blob_id")),
                        total_duration_sec=float(result.get("total_duration_sec", 0)),  # type: ignore[arg-type]
                        chunk_count=int(result.get("chunk_count", 0)),  # type: ignore[arg-type]
                    )
                    await svc.complete_workflow(workflow_id, wf_result)
                    await session.commit()

    return result


@content_pipeline.on_failure_task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=2),
)
async def cleanup_gpu_pod(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Terminate any active GPU pods when the workflow fails (cost-control)."""
    result = await cleanup.execute(input, ctx)

    # Mark workflow as FAILED
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is not None:
        session_maker = _db.async_session_maker
        if session_maker is not None:
            async with session_maker() as session:
                svc = WorkflowService(session)
                await svc.fail_workflow(workflow_id, "Workflow failed — cleanup invoked")
                await session.commit()

    return result


WORKFLOWS.append(content_pipeline)

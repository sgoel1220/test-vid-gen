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

import functools
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Optional

from hatchet_sdk import Context

from app.models.enums import StepName
from app.models.schemas import WorkflowInputSchema, WorkflowResultSchema
from app.services.workflow_service import WorkflowService, get_optional_workflow_id

from . import WORKFLOWS, hatchet
from .db_helpers import ensure_db, get_session_maker
from .steps import cleanup, image, stitch, story, tts

log = logging.getLogger(__name__)

# Type alias for step executor functions
_StepExecutor = Callable[[WorkflowInputSchema, Context], Awaitable[dict[str, object]]]


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


content_pipeline = hatchet.workflow(
    name="ContentPipeline",
    input_validator=WorkflowInputSchema,
)


# ---------------------------------------------------------------------------
# Step lifecycle tracking
# ---------------------------------------------------------------------------

async def _track_step_start(step_name: StepName, ctx: Context) -> None:
    """Record step start in the Workflow and WorkflowStep tables."""
    await ensure_db()
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        return
    async with get_session_maker()() as session:
        await WorkflowService(session).start_step(workflow_id, step_name)
        await session.commit()


async def _track_step_complete(step_name: StepName, ctx: Context) -> None:
    """Record step completion in the Workflow and WorkflowStep tables."""
    await ensure_db()
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        return
    async with get_session_maker()() as session:
        await WorkflowService(session).complete_step(workflow_id, step_name)
        await session.commit()


async def _track_step_failure(step_name: StepName, ctx: Context, error: str) -> None:
    """Record step failure in the Workflow and WorkflowStep tables."""
    await ensure_db()
    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        return
    async with get_session_maker()() as session:
        await WorkflowService(session).fail_step(workflow_id, step_name, error)
        await session.commit()


def tracked_step(
    step_name: StepName,
    executor: _StepExecutor,
) -> _StepExecutor:
    """Wrap *executor* with start/complete/failure tracking.

    Eliminates the repeated try/except/track boilerplate for steps that
    follow the standard lifecycle pattern.
    """

    @functools.wraps(executor)
    async def wrapper(
        input: WorkflowInputSchema, ctx: Context
    ) -> dict[str, object]:
        await _track_step_start(step_name, ctx)
        try:
            result = await executor(input, ctx)
        except Exception as exc:
            await _track_step_failure(step_name, ctx, str(exc))
            raise
        await _track_step_complete(step_name, ctx)
        return result

    return wrapper


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=15),
    retries=2,
)
async def generate_story(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate story from premise using the LLM pipeline."""
    return await tracked_step(StepName.GENERATE_STORY, story.execute)(input, ctx)


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=30),
    retries=2,
    parents=[generate_story],
)
async def tts_synthesis(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Synthesize audio for the story via TTS server on a GPU pod."""
    return await tracked_step(StepName.TTS_SYNTHESIS, tts.execute)(input, ctx)


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=45),
    retries=2,
    parents=[tts_synthesis],
)
async def image_generation(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate images for each story chunk on a GPU pod."""
    return await tracked_step(StepName.IMAGE_GENERATION, image.execute)(input, ctx)


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
    await _track_step_complete(StepName.STITCH_FINAL, ctx)

    # Mark workflow complete (skip if stitch was skipped)
    if not result.get("skipped"):
        workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
        if workflow_id is not None:
            async with get_session_maker()() as session:
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
        async with get_session_maker()() as session:
            svc = WorkflowService(session)
            await svc.fail_workflow(workflow_id, "Workflow failed — cleanup invoked")
            await session.commit()

    return result


WORKFLOWS.append(content_pipeline)

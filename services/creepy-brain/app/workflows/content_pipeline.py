"""ContentPipeline workflow definition.

End-to-end content pipeline: Story → TTS → Images → Stitch.

Step order and timeouts:
    generate_story     (local, 15 min, 2 retries)
    tts_synthesis      (GPU pod, 30 min, 2 retries)
    image_generation   (GPU pod, 45 min, 2 retries)
    stitch_final       (local, 5 min)
    cleanup_gpu_pod    (on_failure hook, 2 min)
"""

from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel

from app.engine import SkippedStepOutput, StepContext, StepDef, WorkflowDef, engine
from app.engine.models import StepOutputMap
from app.models.json_schemas import WorkflowInputSchema, WorkflowResultSchema
from app.services.workflow_service import WorkflowService, get_optional_workflow_id

from .db_helpers import ensure_db, get_session_maker
from .steps import cleanup, image, stitch, story, tts

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Workflow lifecycle hooks
# ---------------------------------------------------------------------------


async def _on_pipeline_complete(workflow_run_id: str, outputs: StepOutputMap) -> None:
    """Mark workflow COMPLETED in DB after all steps succeed."""
    stitch_out = outputs.get("stitch_final")
    if isinstance(stitch_out, SkippedStepOutput):
        log.info("on_complete: stitch_final was skipped, not updating workflow DB")
        return
    if not isinstance(stitch_out, stitch.StitchStepOutput):
        log.warning("on_complete: stitch_final output missing or wrong type")
        return
    workflow_id = _to_uuid(workflow_run_id)
    if workflow_id is None:
        return
    await ensure_db()
    async with get_session_maker()() as session:
        svc = WorkflowService(session)
        wf_result = WorkflowResultSchema(
            story_id=None,
            run_id=None,
            final_audio_blob_id=_to_uuid(stitch_out.final_audio_blob_id),
            final_video_blob_id=_to_uuid(stitch_out.final_video_blob_id),
            total_duration_sec=stitch_out.total_duration_sec,
            chunk_count=stitch_out.chunk_count,
            gpu_pod_id=None,
            total_cost_cents=None,
        )
        await svc.complete_workflow(workflow_id, wf_result)
        await session.commit()


async def _cleanup_gpu_pod(
    input: WorkflowInputSchema, ctx: StepContext
) -> BaseModel:
    """Terminate active GPU pods on workflow failure.

    The runner marks the workflow FAILED after this step completes — no need to
    call fail_workflow here. This step is intentionally not tracked in the
    WorkflowStep DB table (cleanup_gpu_pod has no StepName enum value).
    """
    return await cleanup.execute(input, ctx)


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

content_pipeline_def = WorkflowDef(
    name="ContentPipeline",
    on_complete=_on_pipeline_complete,
    steps=[
        StepDef(
            name="generate_story",
            fn=story.execute,
            timeout_sec=3600,
            max_retries=2,
            auto_pause_after=True,
        ),
        StepDef(
            name="tts_synthesis",
            fn=tts.execute,
            parents=["generate_story"],
            timeout_sec=86400,
            max_retries=2,
        ),
        StepDef(
            name="image_generation",
            fn=image.execute,
            parents=["tts_synthesis"],
            timeout_sec=86400,
            max_retries=2,
        ),
        StepDef(
            name="stitch_final",
            fn=stitch.execute,
            parents=["image_generation"],
            timeout_sec=3600,
        ),
        StepDef(
            name="cleanup_gpu_pod",
            fn=_cleanup_gpu_pod,
            timeout_sec=120,
            is_on_failure=True,
        ),
    ],
)

engine.register(content_pipeline_def)

"""ContentPipeline workflow definition.

End-to-end content pipeline: Story → TTS → Images → Stitch.

Step order and timeouts:
    generate_story     (local, 15 min, 2 retries)
    tts_synthesis      (GPU pod, 30 min)
    image_generation   (GPU pod, 10 min)
    stitch_final       (local, 5 min)
    cleanup_gpu_pod    (on_failure hook, 2 min)
"""

from datetime import timedelta

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema

from . import WORKFLOWS, hatchet
from .steps import cleanup, image, stitch, story, tts

content_pipeline = hatchet.workflow(
    name="ContentPipeline",
    input_validator=WorkflowInputSchema,
)


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=15),
    retries=2,
)
async def generate_story(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate story from premise using the LLM pipeline."""
    return await story.execute(input, ctx)


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=30),
    parents=[generate_story],
)
async def tts_synthesis(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Synthesize audio for the story via TTS server on a GPU pod."""
    return await tts.execute(input, ctx)


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=10),
    parents=[tts_synthesis],
)
async def image_generation(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate images for each story chunk on a GPU pod."""
    return await image.execute(input, ctx)


@content_pipeline.task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=5),
    parents=[image_generation],
)
async def stitch_final(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Stitch audio and images into the final video."""
    return await stitch.execute(input, ctx)


@content_pipeline.on_failure_task(  # type: ignore[untyped-decorator]  # hatchet_sdk has no type stubs
    execution_timeout=timedelta(minutes=2),
)
async def cleanup_gpu_pod(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Terminate any active GPU pods when the workflow fails (cost-control)."""
    return await cleanup.execute(input, ctx)


WORKFLOWS.append(content_pipeline)

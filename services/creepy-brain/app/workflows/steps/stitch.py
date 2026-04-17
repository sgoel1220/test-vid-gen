"""stitch_final step executor.

Full implementation tracked in bead ea6.
"""

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Stitch audio chunks and images into the final video.

    Returns early when ``stitch_video=False`` so the ContentPipeline can
    complete story+TTS runs without requiring the full stitch implementation.

    Raises:
        NotImplementedError: Until bead ea6 is implemented (when stitch_video=True).
    """
    if not input.stitch_video:
        return {"skipped": True, "reason": "stitch_video=False"}
    raise NotImplementedError("stitch_final step not yet implemented (see bead ea6)")

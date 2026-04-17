"""image_generation step executor.

Full implementation tracked in bead 83y.
"""

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate images for each story chunk using a GPU pod.

    Returns early when ``generate_images=False`` so the ContentPipeline can
    complete story+TTS runs without requiring the full image implementation.

    Raises:
        NotImplementedError: Until bead 83y is implemented (when generate_images=True).
    """
    if not input.generate_images:
        return {"skipped": True, "reason": "generate_images=False"}
    raise NotImplementedError("image_generation step not yet implemented (see bead 83y)")

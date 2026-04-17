"""stitch_final step executor.

Full implementation tracked in bead ea6.
"""

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Stitch audio chunks and images into the final video.

    Raises:
        NotImplementedError: Until bead ea6 is implemented.
    """
    raise NotImplementedError("stitch_final step not yet implemented (see bead ea6)")

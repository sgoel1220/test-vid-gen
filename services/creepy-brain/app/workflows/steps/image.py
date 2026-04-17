"""image_generation step executor.

Full implementation tracked in bead 83y.
"""

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate images for each story chunk using a GPU pod.

    Raises:
        NotImplementedError: Until bead 83y is implemented.
    """
    raise NotImplementedError("image_generation step not yet implemented (see bead 83y)")

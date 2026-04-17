"""generate_story step executor.

Full implementation tracked in bead bjx.
"""

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Generate a story from the workflow premise using the LLM pipeline.

    Raises:
        NotImplementedError: Until bead bjx is implemented.
    """
    raise NotImplementedError("generate_story step not yet implemented (see bead bjx)")

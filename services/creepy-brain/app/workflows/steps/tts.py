"""tts_synthesis step executor.

Full implementation tracked in bead duy.
"""

from hatchet_sdk import Context

from app.models.schemas import WorkflowInputSchema


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Synthesize audio via TTS server running on a GPU pod.

    Raises:
        NotImplementedError: Until bead duy is implemented.
    """
    raise NotImplementedError("tts_synthesis step not yet implemented (see bead duy)")

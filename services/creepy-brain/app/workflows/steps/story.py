"""generate_story step executor."""

from __future__ import annotations

import logging
import uuid

from app.engine import StepContext

from app.models.enums import StoryStatus
from app.models.schemas import GenerateStoryStepOutput, WorkflowInputSchema
from app.pipeline import orchestrator
from app.services.story_service import StoryService
from app.workflows.db_helpers import ensure_db, get_session_maker

log = logging.getLogger(__name__)


async def execute(input: WorkflowInputSchema, ctx: StepContext) -> dict[str, object]:
    """Generate a story from the workflow premise using the LLM pipeline.

    1. Ensures the database is initialised (safe to call from the worker process).
    2. Creates a story record in PENDING state.
    3. Runs the full architect -> writer -> reviewer pipeline.
    4. Verifies the story reached COMPLETED status.
    5. Returns a ``GenerateStoryStepOutput`` dict.

    Raises:
        RuntimeError: If the pipeline did not complete successfully.
    """
    await ensure_db()
    session_maker = get_session_maker()

    premise: str = input.premise

    # Create the story row.
    # story.workflow_id is intentionally left null: assigning ctx.workflow_run_id
    # would violate the FK until a Workflow row for that run ID exists.
    # Cross-reference can be added once Workflow creation is in place.
    async with session_maker() as session:
        svc = StoryService(session)
        story = await svc.create(premise=premise)
        await session.commit()
        story_id: uuid.UUID = story.id

    log.info("story %s created, starting pipeline", story_id)

    # Run the full LLM pipeline.  run_pipeline manages its own commits and
    # swallows all exceptions (marks story as FAILED).  We check status below.
    async with session_maker() as session:
        await orchestrator.run_pipeline(
            story_id=story_id,
            premise=premise,
            session=session,
        )

    # Require COMPLETED -- any other terminal state (FAILED, PENDING, etc.)
    # means the pipeline did not finish successfully.
    async with session_maker() as session:
        svc = StoryService(session)
        completed_story = await svc.get(story_id)

    if completed_story is None:
        raise RuntimeError(f"Story {story_id} not found after pipeline run")

    if completed_story.status != StoryStatus.COMPLETED:
        raise RuntimeError(
            f"Story generation did not complete: story_id={story_id} "
            f"status={completed_story.status}"
        )

    log.info(
        "story %s complete: %d words, %d acts",
        story_id,
        completed_story.word_count or 0,
        len(completed_story.acts),
    )

    return GenerateStoryStepOutput(
        story_id=story_id,
        title=completed_story.title or "",
        word_count=completed_story.word_count or 0,
        act_count=len(completed_story.acts),
    ).model_dump()

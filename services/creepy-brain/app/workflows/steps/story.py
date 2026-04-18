"""generate_story step executor."""

from __future__ import annotations

import logging
import uuid

from app.engine import StepContext

from app.models.enums import StoryStatus
from app.models.schemas import GenerateStoryStepOutput, WorkflowInputSchema
from app.pipeline import orchestrator
from app.services import story_service
from app.workflows.db_helpers import ensure_db, get_session_maker

log = logging.getLogger(__name__)


async def execute(input: WorkflowInputSchema, ctx: StepContext) -> GenerateStoryStepOutput:
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

    # Parse the workflow_run_id into a UUID.  The Workflow DB row already
    # exists at this point (created by the API layer before engine.trigger()),
    # so setting workflow_id is safe and allows Story-to-Workflow joins.
    workflow_uuid: uuid.UUID | None = None
    try:
        workflow_uuid = uuid.UUID(ctx.workflow_run_id)
    except ValueError:
        log.warning("story step: could not parse workflow_run_id=%r as UUID", ctx.workflow_run_id)

    async with session_maker() as session:
        story = await story_service.create(session, premise=premise, workflow_id=workflow_uuid)
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
        completed_story = await story_service.get(session, story_id)

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
    )

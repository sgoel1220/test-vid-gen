"""generate_story step executor."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.engine import StepContext

from app.models.enums import StoryStatus
from app.models.json_schemas import GenerateStoryStepOutput, WorkflowInputSchema
from app.models.story import Story
from app.pipeline import orchestrator
from app.services.story_service import StoryService
from app.workflows.db_helpers import get_session_maker

log = logging.getLogger(__name__)


async def execute(input: WorkflowInputSchema, ctx: StepContext) -> GenerateStoryStepOutput:
    """Generate a story from the workflow premise using the LLM pipeline.

    Supports resume: if a COMPLETED story already exists for this workflow_id,
    returns the existing result without re-generating.

    Raises:
        RuntimeError: If the pipeline did not complete successfully.
    """
    await _ensure_db()

    # _ensure_db() guarantees async_session_maker is initialised.
    session_maker = get_session_maker()

    premise: str = input.premise

    # Parse the workflow_run_id into a UUID.
    workflow_uuid: uuid.UUID | None = None
    try:
        workflow_uuid = uuid.UUID(ctx.workflow_run_id)
    except ValueError:
        log.warning("story step: could not parse workflow_run_id=%r as UUID", ctx.workflow_run_id)

    # --- Resume check: look for an existing COMPLETED story for this workflow ---
    if workflow_uuid is not None:
        async with session_maker() as session:
            result = await session.execute(
                select(Story)
                .options(selectinload(Story.acts))
                .where(
                    Story.workflow_id == workflow_uuid,
                    Story.status == StoryStatus.COMPLETED,
                )
                .order_by(Story.created_at.desc())
                .limit(1)
            )
            existing_story = result.scalar_one_or_none()

        if existing_story is not None:
            log.info(
                "story step: resuming — found existing COMPLETED story %s for workflow %s",
                existing_story.id,
                workflow_uuid,
            )
            return GenerateStoryStepOutput(
                story_id=existing_story.id,
                title=existing_story.title or "",
                word_count=existing_story.word_count or 0,
                act_count=len(existing_story.acts),
            )

    # --- Manual override: caller provided story text directly, skip LLM ---
    if input.manual_story_text:
        async with session_maker() as session:
            story = await story_service.create(session, premise=premise, workflow_id=workflow_uuid)
            await session.flush()
            story_id: uuid.UUID = story.id
            word_count = len(input.manual_story_text.split())
            await story_service.upsert_act(
                session,
                story_id=story_id,
                act_number=1,
                title="Manual Story",
                content=input.manual_story_text,
                word_count=word_count,
            )
            await story_service.complete_story(
                session,
                story_id=story_id,
                full_text=input.manual_story_text,
                word_count=word_count,
            )
            await session.commit()

        log.info("story step: manual override — story %s saved (%d words)", story_id, word_count)
        return GenerateStoryStepOutput(
            story_id=story_id,
            title="",
            word_count=word_count,
            act_count=1,
        )

    # --- Normal path: create + run pipeline ---
    async with session_maker() as session:
        story = await story_service.create(session, premise=premise, workflow_id=workflow_uuid)
        await session.commit()
        story_id = story.id

    log.info("story %s created, starting pipeline", story_id)

    from app.llm.client import set_llm_workflow_context
    set_llm_workflow_context(workflow_uuid)
    try:
        async with session_maker() as session:
            await orchestrator.run_pipeline(
                story_id=story_id,
                premise=premise,
                session=session,
                target_word_count=input.target_word_count,
            )
    finally:
        set_llm_workflow_context(None)

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

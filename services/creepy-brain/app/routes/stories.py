"""Stories API routes: /api/stories/*"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.story import Story
from app.services.http_errors import require_found
from app.schemas.story import (
    ActResponse,
    GenerateStoryRequest,
    GenerateStoryResponse,
    StoryListItem,
    StoryResponse,
    UpdateStoryRequest,
)
from app.services import story_service

log = structlog.get_logger()

router = APIRouter(prefix="/api/stories", tags=["stories"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _story_to_response(story: Story) -> StoryResponse:
    return StoryResponse(
        id=story.id,
        title=story.title,
        premise=story.premise,
        status=story.status,
        word_count=story.word_count,
        full_text=story.full_text,
        acts=[
            ActResponse(
                act_number=act.act_number,
                title=act.title,
                content=act.content,
                word_count=act.word_count,
            )
            for act in story.acts
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/generate", response_model=GenerateStoryResponse, status_code=202)
async def generate_story(
    request: Request,
    body: GenerateStoryRequest,
    session: AsyncSession = Depends(get_session),
) -> GenerateStoryResponse:
    """Start background story generation from a premise.

    Returns story_id immediately. Poll GET /api/stories/{id} for progress.
    """
    from app.pipeline import orchestrator

    story = await story_service.create(session, body.premise)
    await session.commit()  # route owns the transaction boundary

    semaphore: asyncio.Semaphore = request.app.state.generation_semaphore

    async def _run() -> None:
        from app.db import async_session_maker

        if async_session_maker is None:
            return
        try:
            async with async_session_maker() as bg_session:
                async with semaphore:
                    await orchestrator.run_pipeline(story.id, body.premise, bg_session)
        except Exception:
            log.exception("pipeline_failed", story_id=str(story.id))
            # Best-effort: mark story as failed in a fresh session
            if async_session_maker is not None:
                try:
                    async with async_session_maker() as fail_session:
                        await story_service.fail_story(fail_session, story.id)
                        await fail_session.commit()
                except Exception:
                    log.exception("pipeline_fail_status_update_failed", story_id=str(story.id))

    # Retain a reference so the task is not garbage-collected mid-flight.
    # Triggers the workflow engine in-process.
    bg_tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    task: asyncio.Task[None] = asyncio.create_task(_run())
    bg_tasks.add(task)
    task.add_done_callback(bg_tasks.discard)

    return GenerateStoryResponse(story_id=story.id, status=story.status)


@router.get("/by-workflow/{workflow_id}", response_model=StoryResponse)
async def get_story_by_workflow(
    workflow_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> StoryResponse:
    """Get story by its parent workflow ID."""
    story = require_found(
        await story_service.get_by_workflow(session, workflow_id),
        "Story not found for this workflow",
    )
    return _story_to_response(story)


@router.put("/{story_id}", response_model=StoryResponse)
async def update_story(
    story_id: uuid.UUID,
    body: UpdateStoryRequest,
    session: AsyncSession = Depends(get_session),
) -> StoryResponse:
    """Update a story's full text (e.g. after manual review)."""
    await story_service.update_full_text(session, story_id, body.full_text)
    await session.commit()
    # Re-fetch with acts loaded
    loaded = require_found(
        await story_service.get(session, story_id), "Story not found"
    )
    return _story_to_response(loaded)


@router.get("/{story_id}", response_model=StoryResponse)
async def get_story(
    story_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> StoryResponse:
    """Get story detail including acts."""
    story = require_found(
        await story_service.get(session, story_id), "Story not found"
    )
    return _story_to_response(story)


@router.get("", response_model=list[StoryListItem])
async def list_stories(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[StoryListItem]:
    """List stories ordered by creation time descending."""
    stories = await story_service.list_stories(session, limit=limit, offset=offset)
    return [
        StoryListItem(
            id=s.id,
            title=s.title,
            premise=s.premise,
            status=s.status,
            word_count=s.word_count,
        )
        for s in stories
    ]

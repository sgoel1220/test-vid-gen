"""Stories API routes: /api/stories/*"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.enums import StoryStatus
from app.models.story import Story
from app.services.http_errors import require_found
from app.services.story_service import StoryService

log = structlog.get_logger()

router = APIRouter(prefix="/api/stories", tags=["stories"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class GenerateStoryRequest(BaseModel):
    premise: str = Field(..., min_length=10, description="Story premise or idea")


class GenerateStoryResponse(BaseModel):
    story_id: uuid.UUID
    status: StoryStatus


class ActResponse(BaseModel):
    act_number: int
    title: Optional[str]
    word_count: Optional[int]


class StoryResponse(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    premise: str
    status: StoryStatus
    word_count: Optional[int]
    acts: list[ActResponse]


class StoryListItem(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    premise: str
    status: StoryStatus
    word_count: Optional[int]


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
        acts=[
            ActResponse(
                act_number=act.act_number,
                title=act.title,
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

    svc = StoryService(session)
    story = await svc.create(body.premise)
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
                        await StoryService(fail_session).fail_story(story.id)
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


@router.get("/{story_id}", response_model=StoryResponse)
async def get_story(
    story_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> StoryResponse:
    """Get story detail including acts."""
    svc = StoryService(session)
    story = require_found(await svc.get(story_id), "Story not found")
    return _story_to_response(story)


@router.get("", response_model=list[StoryListItem])
async def list_stories(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[StoryListItem]:
    """List stories ordered by creation time descending."""
    svc = StoryService(session)
    stories = await svc.list_stories(limit=limit, offset=offset)
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

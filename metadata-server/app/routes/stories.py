"""Story CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from creepy_pasta_protocol.common import Frozen
from creepy_pasta_protocol.stories import (
    GenerateStoryRequest,
    PatchStoryRequest,
    StoryActDTO,
    StoryDetailDTO,
    StorySummaryDTO,
)

from app.auth import require_api_key
from app.converters import story_act_to_dto, story_to_detail, story_to_summary
from app.db import DbSession
from app.services import stories as stories_svc


class _UpsertActBody(Frozen):
    title: str
    target_word_count: int
    text: str


router = APIRouter(
    prefix="/v1/stories",
    tags=["stories"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=StoryDetailDTO)
async def create_story(
    body: GenerateStoryRequest, session: DbSession
) -> StoryDetailDTO:
    story = await stories_svc.create(session, body)
    detail = await stories_svc.get_detail(session, story.id)
    await session.commit()
    return story_to_detail(detail)


@router.patch("/{story_id}", response_model=StoryDetailDTO)
async def patch_story(
    story_id: uuid.UUID, body: PatchStoryRequest, session: DbSession
) -> StoryDetailDTO:
    await stories_svc.patch(session, story_id, body)
    detail = await stories_svc.get_detail(session, story_id)
    await session.commit()
    return story_to_detail(detail)


@router.get("", response_model=list[StorySummaryDTO])
async def list_stories(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StorySummaryDTO]:
    stories = await stories_svc.get_summary_list(session, limit=limit, offset=offset)
    return [story_to_summary(s) for s in stories]


@router.get("/{story_id}", response_model=StoryDetailDTO)
async def get_story(story_id: uuid.UUID, session: DbSession) -> StoryDetailDTO:
    story = await stories_svc.get_detail(session, story_id)
    return story_to_detail(story)


@router.post("/{story_id}/acts/{act_number}", response_model=StoryActDTO)
async def upsert_act(
    story_id: uuid.UUID,
    act_number: int,
    body: _UpsertActBody,
    session: DbSession,
) -> StoryActDTO:
    act = await stories_svc.upsert_act(
        session,
        story_id,
        act_number,
        body.title,
        body.target_word_count,
        body.text,
    )
    await session.commit()
    return story_act_to_dto(act)


@router.post("/{story_id}/recalculate-words", response_model=StoryDetailDTO)
async def recalculate_words(
    story_id: uuid.UUID, session: DbSession
) -> StoryDetailDTO:
    story = await stories_svc.update_total_word_count(session, story_id)
    await session.commit()
    return story_to_detail(story)

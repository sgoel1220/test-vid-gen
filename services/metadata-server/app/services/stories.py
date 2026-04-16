"""Story service — CRUD for generated stories."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from creepy_pasta_protocol.stories import (
    GenerateStoryRequest,
    PatchStoryRequest,
    StoryStatus,
)

from app.converters import patch_story_request_apply
from app.models import Story, StoryAct


async def create(session: AsyncSession, req: GenerateStoryRequest) -> Story:
    story = Story(
        id=uuid.uuid4(),
        premise=req.premise,
        label=req.label,
        status=StoryStatus.PENDING,
        review_loops=0,
        created_at=datetime.now(timezone.utc),
    )
    session.add(story)
    await session.flush()
    return story


async def patch(
    session: AsyncSession, story_id: uuid.UUID, req: PatchStoryRequest
) -> Story:
    result = await session.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one()
    patch_story_request_apply(story, req)
    await session.flush()
    return story


async def get_detail(session: AsyncSession, story_id: uuid.UUID) -> Story:
    result = await session.execute(
        select(Story)
        .where(Story.id == story_id)
        .options(selectinload(Story.acts))
    )
    return result.scalar_one()


async def get_summary_list(
    session: AsyncSession, limit: int, offset: int
) -> list[Story]:
    result = await session.execute(
        select(Story).order_by(Story.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def upsert_act(
    session: AsyncSession,
    story_id: uuid.UUID,
    act_number: int,
    title: str,
    target_word_count: int,
    text: str,
) -> StoryAct:
    """Create or replace an act for a story."""
    word_count = len(text.split())
    now = datetime.now(timezone.utc)

    result = await session.execute(
        select(StoryAct).where(
            StoryAct.story_id == story_id,
            StoryAct.act_number == act_number,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.title = title
        existing.target_word_count = target_word_count
        existing.text = text
        existing.word_count = word_count
        existing.updated_at = now
        await session.flush()
        return existing

    act = StoryAct(
        id=uuid.uuid4(),
        story_id=story_id,
        act_number=act_number,
        title=title,
        target_word_count=target_word_count,
        text=text,
        word_count=word_count,
        created_at=now,
    )
    session.add(act)
    await session.flush()
    return act


async def update_total_word_count(
    session: AsyncSession, story_id: uuid.UUID
) -> Story:
    """Recalculate total_word_count from acts."""
    story_detail = await get_detail(session, story_id)
    story_detail.total_word_count = sum(a.word_count for a in story_detail.acts)
    await session.flush()
    return story_detail

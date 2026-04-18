"""Story CRUD operations using SQLAlchemy.

Transaction ownership convention
---------------------------------
Services in this codebase only *flush* — they stage changes to the DB
session but never commit.  The *caller* (route handler or pipeline
orchestrator) is responsible for calling ``await session.commit()``
after each logical unit of work.  This makes transaction intent
explicit at the point of use and eliminates double-commit risk.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import StoryStatus
from app.models.json_schemas import StoryActOutline, StoryOutlineSchema
from app.models.story import Story, StoryAct
from app.pipeline.models import FiveActOutline, StoryBible


async def create(
    session: AsyncSession,
    premise: str,
    workflow_id: uuid.UUID | None = None,
) -> Story:
    """Create a new story record in pending state (flush only).

    Args:
        session: The active async DB session.
        premise: The story premise text.
        workflow_id: Optional FK to the owning Workflow row.  Pass this
            whenever a Workflow DB row already exists so the story can be
            joined to its workflow.
    """
    story = Story(premise=premise, status=StoryStatus.PENDING, workflow_id=workflow_id)
    session.add(story)
    await session.flush()
    await session.refresh(story)
    return story


async def get(session: AsyncSession, story_id: uuid.UUID) -> Story | None:
    """Get story by ID with acts eagerly loaded."""
    result = await session.execute(
        select(Story)
        .options(selectinload(Story.acts))
        .where(Story.id == story_id)
    )
    return result.scalar_one_or_none()


async def list_stories(
    session: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Story]:
    """List stories ordered by creation time descending."""
    result = await session.execute(
        select(Story).order_by(Story.created_at.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()


async def update_status(
    session: AsyncSession,
    story_id: uuid.UUID,
    status: StoryStatus,
) -> None:
    """Update story status (flush only)."""
    story = await _get_or_raise(session, story_id)
    story.status = status
    await session.flush()


async def update_bible_and_outline(
    session: AsyncSession,
    story_id: uuid.UUID,
    bible: StoryBible,
    outline: FiveActOutline,
) -> None:
    """Persist architect output: title and a simplified outline JSONB (flush only)."""
    story = await _get_or_raise(session, story_id)
    story.title = bible.title

    acts_summary: list[StoryActOutline] = [
        StoryActOutline(
            act_number=act.act_number,
            title=act.title,
            summary=act.act_hook,
            target_word_count=act.target_word_count,
            key_events=[b.description for b in act.beats],
        )
        for act in outline.acts
    ]
    story.outline = StoryOutlineSchema(
        title=bible.title,
        total_acts=len(acts_summary),
        total_target_words=sum(a.target_word_count for a in acts_summary),
        acts=acts_summary,
        themes=[],
        setting=bible.setting.location,
        tone=bible.horror_rules.horror_subgenre,
    )
    await session.flush()


async def upsert_act(
    session: AsyncSession,
    story_id: uuid.UUID,
    act_number: int,
    title: str,
    content: str,
    word_count: int,
) -> None:
    """Insert or update a story act (flush only)."""
    result = await session.execute(
        select(StoryAct).where(
            StoryAct.story_id == story_id,
            StoryAct.act_number == act_number,
        )
    )
    act = result.scalar_one_or_none()
    if act is None:
        act = StoryAct(
            story_id=story_id,
            act_number=act_number,
            title=title,
            content=content,
            word_count=word_count,
        )
        session.add(act)
    else:
        act.title = title
        act.content = content
        act.word_count = word_count
        act.revision_count += 1
    await session.flush()


async def complete_story(
    session: AsyncSession,
    story_id: uuid.UUID,
    full_text: str,
    word_count: int,
) -> None:
    """Mark story as completed with final text (flush only)."""
    story = await _get_or_raise(session, story_id)
    story.status = StoryStatus.COMPLETED
    story.full_text = full_text
    story.word_count = word_count
    story.completed_at = datetime.now(timezone.utc)
    await session.flush()


async def fail_story(session: AsyncSession, story_id: uuid.UUID) -> None:
    """Mark story as failed (flush only)."""
    story = await _get_or_raise(session, story_id)
    story.status = StoryStatus.FAILED
    await session.flush()


async def _get_or_raise(session: AsyncSession, story_id: uuid.UUID) -> Story:
    result = await session.execute(select(Story).where(Story.id == story_id))
    story = result.scalar_one_or_none()
    if story is None:
        raise ValueError(f"Story {story_id} not found")
    return story

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
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import StoryStatus
from app.models.schemas import StoryActOutline, StoryOutlineSchema
from app.models.story import Story, StoryAct
from app.pipeline.models import FiveActOutline, StoryBible


class StoryService:
    """Story and StoryAct database operations.

    Each mutating method flushes but does not commit; the caller owns
    the transaction boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, premise: str) -> Story:
        """Create a new story record in pending state (flush only)."""
        story = Story(premise=premise, status=StoryStatus.PENDING)
        self._session.add(story)
        await self._session.flush()
        await self._session.refresh(story)
        return story

    async def get(self, story_id: uuid.UUID) -> Optional[Story]:
        """Get story by ID with acts eagerly loaded."""
        result = await self._session.execute(
            select(Story)
            .options(selectinload(Story.acts))
            .where(Story.id == story_id)
        )
        return result.scalar_one_or_none()

    async def list_stories(
        self, limit: int = 50, offset: int = 0
    ) -> Sequence[Story]:
        """List stories ordered by creation time descending."""
        result = await self._session.execute(
            select(Story).order_by(Story.created_at.desc()).limit(limit).offset(offset)
        )
        return result.scalars().all()

    async def update_status(self, story_id: uuid.UUID, status: StoryStatus) -> None:
        """Update story status (flush only)."""
        story = await self._get_or_raise(story_id)
        story.status = status
        await self._session.flush()

    async def update_bible_and_outline(
        self,
        story_id: uuid.UUID,
        bible: StoryBible,
        outline: FiveActOutline,
    ) -> None:
        """Persist architect output: title and a simplified outline JSONB (flush only)."""
        story = await self._get_or_raise(story_id)
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
        await self._session.flush()

    async def upsert_act(
        self,
        story_id: uuid.UUID,
        act_number: int,
        title: str,
        content: str,
        word_count: int,
    ) -> None:
        """Insert or update a story act (flush only)."""
        result = await self._session.execute(
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
            self._session.add(act)
        else:
            act.title = title
            act.content = content
            act.word_count = word_count
            act.revision_count += 1
        await self._session.flush()

    async def complete_story(
        self, story_id: uuid.UUID, full_text: str, word_count: int
    ) -> None:
        """Mark story as completed with final text (flush only)."""
        story = await self._get_or_raise(story_id)
        story.status = StoryStatus.COMPLETED
        story.full_text = full_text
        story.word_count = word_count
        story.completed_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def fail_story(self, story_id: uuid.UUID) -> None:
        """Mark story as failed (flush only)."""
        story = await self._get_or_raise(story_id)
        story.status = StoryStatus.FAILED
        await self._session.flush()

    async def _get_or_raise(self, story_id: uuid.UUID) -> Story:
        result = await self._session.execute(
            select(Story).where(Story.id == story_id)
        )
        story = result.scalar_one_or_none()
        if story is None:
            raise ValueError(f"Story {story_id} not found")
        return story

"""Story CRUD operations using SQLAlchemy."""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import StoryStatus
from app.models.story import Story, StoryAct


class StoryService:
    """Story and StoryAct database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, premise: str) -> Story:
        """Create a new story record in pending state."""
        story = Story(premise=premise, status=StoryStatus.PENDING)
        self._session.add(story)
        await self._session.commit()
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
        """Update story status."""
        story = await self._get_or_raise(story_id)
        story.status = status
        await self._session.commit()

    async def update_bible_and_outline(
        self,
        story_id: uuid.UUID,
        bible_json: dict[str, Any],
        outline_json: dict[str, Any],
        title: Optional[str] = None,
    ) -> None:
        """Persist architect output (title not stored directly — stored in acts)."""
        story = await self._get_or_raise(story_id)
        if title:
            story.title = title
        # Store outline in the story's outline JSONB field
        from app.models.schemas import StoryOutlineSchema

        # Build a simplified outline summary for the JSONB field
        acts_summary = [
            {
                "act_number": act.get("act_number", i + 1),
                "title": act.get("title", ""),
                "summary": act.get("act_hook", ""),
                "target_word_count": act.get("target_word_count", 0),
                "key_events": [b.get("description", "") for b in act.get("beats", [])],
            }
            for i, act in enumerate(outline_json.get("acts", []))
        ]
        outline_schema = StoryOutlineSchema(
            title=title or "",
            total_acts=len(acts_summary),
            total_target_words=sum(a["target_word_count"] for a in acts_summary),
            acts=acts_summary,  # type: ignore[arg-type]
            themes=[],
            setting=bible_json.get("setting", {}).get("location", ""),
            tone=bible_json.get("horror_rules", {}).get("horror_subgenre", ""),
        )
        story.outline = outline_schema
        await self._session.commit()

    async def upsert_act(
        self,
        story_id: uuid.UUID,
        act_number: int,
        title: str,
        content: str,
        word_count: int,
    ) -> None:
        """Insert or update a story act."""
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
        await self._session.commit()

    async def complete_story(
        self, story_id: uuid.UUID, full_text: str, word_count: int
    ) -> None:
        """Mark story as completed with final text."""
        from datetime import datetime

        story = await self._get_or_raise(story_id)
        story.status = StoryStatus.COMPLETED
        story.full_text = full_text
        story.word_count = word_count
        story.completed_at = datetime.utcnow()
        await self._session.commit()

    async def fail_story(self, story_id: uuid.UUID, error: str) -> None:
        """Mark story as failed."""
        story = await self._get_or_raise(story_id)
        story.status = StoryStatus.FAILED
        await self._session.commit()

    async def _get_or_raise(self, story_id: uuid.UUID) -> Story:
        result = await self._session.execute(
            select(Story).where(Story.id == story_id)
        )
        story = result.scalar_one_or_none()
        if story is None:
            raise ValueError(f"Story {story_id} not found")
        return story

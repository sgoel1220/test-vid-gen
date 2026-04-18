"""Voice service — CRUD for voice references."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice import Voice


class VoiceCreateResult(BaseModel):
    """Result of an idempotent voice create operation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    voice: Voice = Field(description="Existing or newly created voice")
    created: bool = Field(description="True when a new voice row was inserted")


async def get_by_name(session: AsyncSession, name: str) -> Voice | None:
    """Return the Voice with *name*, or None if it does not exist."""
    result = await session.execute(select(Voice).where(Voice.name == name))
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    name: str,
    audio_path: str,
    description: str | None = None,
    is_default: bool = False,
) -> Voice:
    """Insert a new voice; does NOT check for duplicates (caller must check)."""
    voice = Voice(
        id=uuid.uuid4(),
        name=name,
        description=description,
        audio_path=audio_path,
        is_default=is_default,
    )
    session.add(voice)
    await session.flush()
    return voice


async def get_or_create(
    session: AsyncSession,
    name: str,
    audio_path: str,
    description: str | None = None,
    is_default: bool = False,
) -> VoiceCreateResult:
    """Return the voice and whether it was created — idempotent by name."""
    existing = await get_by_name(session, name)
    if existing is not None:
        return VoiceCreateResult(voice=existing, created=False)
    voice = await create(session, name, audio_path, description, is_default)
    return VoiceCreateResult(voice=voice, created=True)


async def list_all(session: AsyncSession) -> list[Voice]:
    """Return all voices ordered by creation time (newest first)."""
    result = await session.execute(select(Voice).order_by(Voice.created_at.desc()))
    return list(result.scalars().all())

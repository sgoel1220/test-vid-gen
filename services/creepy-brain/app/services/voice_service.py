"""Voice service — CRUD for voice references."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice import Voice


async def get_by_name(session: AsyncSession, name: str) -> Optional[Voice]:
    """Return the Voice with *name*, or None if it does not exist."""
    result = await session.execute(select(Voice).where(Voice.name == name))
    return result.scalar_one_or_none()


async def create(
    session: AsyncSession,
    name: str,
    audio_path: str,
    description: Optional[str] = None,
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
    description: Optional[str] = None,
    is_default: bool = False,
) -> tuple[Voice, bool]:
    """Return (voice, created) — idempotent create-or-get by name."""
    existing = await get_by_name(session, name)
    if existing is not None:
        return existing, False
    voice = await create(session, name, audio_path, description, is_default)
    return voice, True


async def list_all(session: AsyncSession) -> list[Voice]:
    """Return all voices ordered by creation time (newest first)."""
    result = await session.execute(select(Voice).order_by(Voice.created_at.desc()))
    return list(result.scalars().all())

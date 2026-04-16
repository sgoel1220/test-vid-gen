"""Voice service — upsert by filename and list."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AudioBlob, Voice


async def get_by_filename(session: AsyncSession, filename: str) -> Optional[Voice]:
    """Return the Voice with *filename*, or None if it does not exist."""
    result = await session.execute(
        select(Voice).where(Voice.filename == filename)
    )
    return result.scalar_one_or_none()


async def upsert(session: AsyncSession, filename: str, audio_blob: AudioBlob) -> Voice:
    """Return an existing Voice for *filename*, or insert and return a new one."""
    existing = await get_by_filename(session, filename)
    if existing is not None:
        return existing
    voice = Voice(
        id=uuid.uuid4(),
        filename=filename,
        audio_blob_id=audio_blob.id,
        duration_sec=audio_blob.duration_sec,
        created_at=datetime.now(timezone.utc),
    )
    session.add(voice)
    await session.flush()
    return voice


async def list_all(session: AsyncSession) -> list[Voice]:
    """Return all voices ordered by creation time (newest first)."""
    result = await session.execute(
        select(Voice).order_by(Voice.created_at.desc())
    )
    return list(result.scalars().all())

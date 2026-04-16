"""Script service — create-or-get by content hash."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Script


async def create_or_get(session: AsyncSession, text: str) -> Script:
    """Return an existing Script for *text*, or insert and return a new one."""
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    result = await session.execute(
        select(Script).where(Script.text_sha256 == text_sha256)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    script = Script(
        id=uuid.uuid4(),
        text=text,
        text_sha256=text_sha256,
        char_count=len(text),
        created_at=datetime.now(timezone.utc),
    )
    session.add(script)
    await session.flush()
    return script


async def get_by_sha256(session: AsyncSession, text_sha256: str) -> Script:
    """Look up a script by its SHA256 hash. Raises if not found."""
    result = await session.execute(
        select(Script).where(Script.text_sha256 == text_sha256)
    )
    return result.scalar_one()

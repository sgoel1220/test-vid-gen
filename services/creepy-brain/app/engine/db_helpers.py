"""Database session helpers for workflow engine internals."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db as _db


def get_optional_session_maker() -> async_sessionmaker[AsyncSession] | None:
    """Return the active DB session maker, if initialization has completed."""
    return _db.async_session_maker


@asynccontextmanager
async def optional_session() -> AsyncIterator[AsyncSession | None]:
    """Yield a DB session, or ``None`` when the DB has not been initialized."""
    session_maker = _db.async_session_maker
    if session_maker is None:
        yield None
        return

    async with session_maker() as session:
        yield session

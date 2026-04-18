"""Shared DB initialization helpers for workflow steps.

Each step runs in-process, DB initialization
must be idempotent.  This module centralises the lock-guarded lazy init
pattern that was previously duplicated across cleanup, content_pipeline,
and other step modules.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

import app.db as _db

_db_init_lock: asyncio.Lock = asyncio.Lock()


async def ensure_db() -> None:
    """Initialize the DB engine if not already done (idempotent, thread-safe)."""
    async with _db_init_lock:
        if _db.async_session_maker is None:
            await _db.init_db()


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the active session maker, raising if DB was not initialized."""
    maker = _db.async_session_maker
    if maker is None:
        raise RuntimeError("DB not initialized — call ensure_db() before starting")
    return maker

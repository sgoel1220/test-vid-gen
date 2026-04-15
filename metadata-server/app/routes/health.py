"""Health check endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.db import get_session
from creepy_pasta_protocol import PROTOCOL_VERSION

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"protocol_version": PROTOCOL_VERSION, "status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    async for session in get_session():
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}

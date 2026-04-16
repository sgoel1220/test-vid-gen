"""Story status proxy — forwards to metadata-server."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth import require_api_key
from app.services.generation import MetadataClient

router = APIRouter(
    prefix="/v1/stories",
    tags=["stories"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{story_id}/status")
async def get_story_status(story_id: str) -> dict[str, Any]:
    """Proxy to metadata-server GET /v1/stories/{id}."""
    meta = MetadataClient()
    try:
        return await meta.get_story(story_id)
    finally:
        await meta.close()


@router.get("")
async def list_stories(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Proxy to metadata-server GET /v1/stories."""
    meta = MetadataClient()
    try:
        return await meta.list_stories(limit=limit, offset=offset)
    finally:
        await meta.close()

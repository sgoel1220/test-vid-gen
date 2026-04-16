"""Generation service — httpx calls to metadata-server + pipeline invocation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Semaphore to limit concurrent generations
_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        settings = get_settings()
        _semaphore = asyncio.Semaphore(settings.max_concurrent_generations)
    return _semaphore


class MetadataClient:
    """Typed HTTP client for metadata-server story endpoints."""

    def __init__(self) -> None:
        settings = get_settings()
        self._base = settings.metadata_server_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {settings.metadata_api_key}"}
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def create_story(
        self, premise: str, label: Optional[str] = None
    ) -> dict[str, Any]:
        """POST /v1/stories — create a new story record."""
        body: dict[str, Any] = {"premise": premise}
        if label is not None:
            body["label"] = label
        resp = await self._client.post(
            f"{self._base}/v1/stories",
            json=body,
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def patch_story(self, story_id: str, **fields: Any) -> dict[str, Any]:
        """PATCH /v1/stories/{id} — partial update."""
        resp = await self._client.patch(
            f"{self._base}/v1/stories/{story_id}",
            json=fields,
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def get_story(self, story_id: str) -> dict[str, Any]:
        """GET /v1/stories/{id} — full detail."""
        resp = await self._client.get(
            f"{self._base}/v1/stories/{story_id}",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def list_stories(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """GET /v1/stories — summary list."""
        resp = await self._client.get(
            f"{self._base}/v1/stories",
            params={"limit": limit, "offset": offset},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def upsert_act(
        self,
        story_id: str,
        act_number: int,
        title: str,
        target_word_count: int,
        text: str,
    ) -> dict[str, Any]:
        """POST /v1/stories/{id}/acts/{n} — upsert act prose."""
        resp = await self._client.post(
            f"{self._base}/v1/stories/{story_id}/acts/{act_number}",
            json={
                "title": title,
                "target_word_count": target_word_count,
                "text": text,
            },
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def recalculate_words(self, story_id: str) -> dict[str, Any]:
        """POST /v1/stories/{id}/recalculate-words."""
        resp = await self._client.post(
            f"{self._base}/v1/stories/{story_id}/recalculate-words",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def start_generation(premise: str, label: Optional[str] = None) -> str:
    """Create story in metadata-server and kick off background pipeline.

    Returns the story_id immediately.
    """
    from app.pipeline import orchestrator

    meta = MetadataClient()
    story_data = await meta.create_story(premise, label)
    story_id: str = story_data["id"]

    async def _run_with_semaphore() -> None:
        async with _get_semaphore():
            try:
                await orchestrator.run_pipeline(story_id, premise, meta)
            finally:
                await meta.close()

    asyncio.create_task(_run_with_semaphore())
    log.info("started generation for story %s", story_id)
    return story_id

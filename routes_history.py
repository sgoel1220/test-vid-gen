"""History proxy routes — forward browser requests to the metadata server.

The browser only ever talks to the pod.  These endpoints proxy to metadata-svc
so the home server is never directly exposed.

TODO: add pod-level auth if the pod itself is ever exposed to the internet.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from creepy_pasta_protocol.runs import RunDetailDTO, RunSummaryDTO

import persistence as _persist

_log = logging.getLogger(__name__)

history_router = APIRouter()


def _require_persistence() -> None:
    if not _persist.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Persistence is not configured — set METADATA_API_URL and METADATA_API_KEY.",
        )


@history_router.get("/api/history", response_model=list[RunSummaryDTO])
async def list_history(limit: int = 50, offset: int = 0) -> list[RunSummaryDTO]:
    _require_persistence()
    async with _persist.get_client() as client:
        return await client.list_runs(limit=limit, offset=offset)


@history_router.get("/api/history/audio/{audio_blob_id}")
async def stream_history_audio(audio_blob_id: str) -> StreamingResponse:
    """Stream audio bytes from the metadata server.

    Content-Type is set to application/octet-stream; the browser can infer
    the format from the audio data itself.
    """
    _require_persistence()
    client = _persist.get_client()

    async def _body():
        try:
            async for chunk in client.stream_audio(audio_blob_id):
                yield chunk
        finally:
            await client.aclose()

    return StreamingResponse(_body(), media_type="application/octet-stream")


@history_router.get("/api/history/{run_id}", response_model=RunDetailDTO)
async def get_history_run(run_id: str) -> RunDetailDTO:
    _require_persistence()
    async with _persist.get_client() as client:
        return await client.get_run(run_id)

"""Blob streaming endpoint with HTTP range request support."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.db import DbSession
from app.services import blob_service

router = APIRouter(prefix="/api/blobs", tags=["blobs"])


@router.get("/{blob_id}")
async def get_blob(blob_id: uuid.UUID, session: DbSession, request: Request) -> Response:
    """Return binary blob data with HTTP range request support for audio/video streaming."""
    blob = await blob_service.get(session, blob_id)
    data: bytes = blob.data
    total = len(data)
    range_header = request.headers.get("Range")

    if range_header:
        # Parse "bytes=start-end"
        try:
            range_spec = range_header.strip().removeprefix("bytes=")
            start_str, _, end_str = range_spec.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else total - 1
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid Range header")

        if start >= total or end >= total or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{total}"},
            )

        chunk = data[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type=blob.mime_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Content-Length": str(len(chunk)),
                "Accept-Ranges": "bytes",
            },
        )

    return Response(
        content=data,
        media_type=blob.mime_type,
        headers={
            "Content-Length": str(total),
            "Accept-Ranges": "bytes",
        },
    )

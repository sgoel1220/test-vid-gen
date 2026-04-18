"""Blob streaming endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.db import DbSession
from app.services import blob_service
from app.services.errors import ResourceNotFoundError

router = APIRouter(prefix="/api/blobs", tags=["blobs"])


@router.get("/{blob_id}")
async def get_blob(blob_id: uuid.UUID, session: DbSession) -> Response:
    """Return binary blob data with its stored MIME type."""
    try:
        blob = await blob_service.get(session, blob_id)
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=blob.data,
        media_type=blob.mime_type,
        headers={"Content-Length": str(blob.size_bytes)},
    )

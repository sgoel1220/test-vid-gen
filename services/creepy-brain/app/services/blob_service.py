"""Blob storage service — persists binary data in Postgres BYTEA via WorkflowBlob."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import BlobType
from app.models.workflow import WorkflowBlob
from app.services.errors import ResourceNotFoundError


async def store(
    session: AsyncSession,
    data: bytes,
    mime_type: str,
    blob_type: BlobType,
    workflow_id: uuid.UUID | None = None,
) -> WorkflowBlob:
    """Persist binary data as a WorkflowBlob row.

    Args:
        session: Active SQLAlchemy async session.
        data: Raw bytes to store.
        mime_type: MIME type string (e.g. "audio/wav").
        blob_type: Semantic type tag from BlobType enum.
        workflow_id: Optional FK to the owning workflow; None for standalone blobs.

    Returns:
        Newly created WorkflowBlob ORM object (flushed but not committed).
    """
    blob = WorkflowBlob(
        id=uuid.uuid4(),
        workflow_id=workflow_id,
        blob_type=blob_type,
        data=data,
        mime_type=mime_type,
        size_bytes=len(data),
    )
    session.add(blob)
    await session.flush()
    return blob


async def get(session: AsyncSession, blob_id: uuid.UUID) -> WorkflowBlob:
    """Fetch a blob by primary key; raises 404 if absent."""
    result = await session.execute(
        select(WorkflowBlob).where(WorkflowBlob.id == blob_id)
    )
    blob = result.scalar_one_or_none()
    if blob is None:
        raise ResourceNotFoundError("Blob", blob_id)
    return blob

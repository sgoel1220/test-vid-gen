"""Chunk service — bulk upsert by (run_id, chunk_index)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from creepy_pasta_protocol.chunks import ChunkSpec

from app.models import Chunk


async def bulk_upsert(
    session: AsyncSession, run_id: uuid.UUID, specs: list[ChunkSpec]
) -> list[Chunk]:
    """Upsert chunks for *run_id* from *specs*; returns the full list in spec order."""
    chunks: list[Chunk] = []
    for spec in specs:
        result = await session.execute(
            select(Chunk).where(
                Chunk.run_id == run_id,
                Chunk.chunk_index == spec.chunk_index,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.text = spec.text
            chunks.append(existing)
        else:
            chunk = Chunk(
                id=uuid.uuid4(),
                run_id=run_id,
                chunk_index=spec.chunk_index,
                text=spec.text,
                attempts_used=0,
            )
            session.add(chunk)
            chunks.append(chunk)
    await session.flush()
    return chunks

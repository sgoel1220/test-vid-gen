"""Chunk bulk-upsert endpoint."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from creepy_pasta_protocol.chunks import ChunkDTO, ChunkSpec

from app.auth import require_api_key
from app.converters import chunk_to_dto
from app.db import DbSession
from app.services import chunks as chunks_svc

router = APIRouter(tags=["chunks"], dependencies=[Depends(require_api_key)])


@router.post("/v1/runs/{run_id}/chunks", response_model=list[ChunkDTO])
async def bulk_create_chunks(
    run_id: uuid.UUID,
    specs: list[ChunkSpec],
    session: DbSession,
) -> list[ChunkDTO]:
    chunks = await chunks_svc.bulk_upsert(session, run_id, specs)
    await session.commit()
    return [chunk_to_dto(c) for c in chunks]

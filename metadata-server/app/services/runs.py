"""Run service — CRUD for TTS runs."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from creepy_pasta_protocol.runs import CreateRunRequest, PatchRunRequest

from app.converters import create_run_request_to_orm_kwargs, patch_run_request_apply
from app.models import Chunk, Run
from app.services import scripts as scripts_svc


async def create(session: AsyncSession, req: CreateRunRequest) -> Run:
    """Create a new run with idempotency support via pod_run_id.

    If pod_run_id is provided and a run with that ID already exists,
    returns the existing run instead of creating a duplicate.
    """
    # Check for existing run by pod_run_id (idempotency)
    if req.pod_run_id is not None:
        result = await session.execute(
            select(Run).where(Run.pod_run_id == req.pod_run_id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

    # Resolve script_sha256 to script_id if needed
    resolved_script_id: uuid.UUID
    if req.script_id is not None:
        resolved_script_id = uuid.UUID(req.script_id)
    elif req.script_sha256 is not None:
        script = await scripts_svc.get_by_sha256(session, req.script_sha256)
        resolved_script_id = script.id
    else:
        raise HTTPException(
            status_code=400,
            detail="Either script_id or script_sha256 must be provided"
        )

    kwargs = create_run_request_to_orm_kwargs(req, resolved_script_id)
    run = Run(**kwargs)
    session.add(run)
    await session.flush()
    return run


async def patch(session: AsyncSession, run_id: uuid.UUID, req: PatchRunRequest) -> Run:
    result = await session.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one()
    patch_run_request_apply(run, req)
    await session.flush()
    return run


async def get_summary_list(
    session: AsyncSession, limit: int, offset: int
) -> list[Run]:
    result = await session.execute(
        select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def get_detail(session: AsyncSession, run_id: uuid.UUID) -> Run:
    result = await session.execute(
        select(Run)
        .where(Run.id == run_id)
        .options(
            selectinload(Run.chunks).selectinload(Chunk.audio_blob),
            selectinload(Run.final_audio),
        )
    )
    return result.scalar_one()


async def get_by_pod_run_id(session: AsyncSession, pod_run_id: str) -> Run:
    """Resolve pod_run_id to Run; raise 404 if not found."""
    result = await session.execute(select(Run).where(Run.pod_run_id == pod_run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run with pod_run_id={pod_run_id} not found")
    return run

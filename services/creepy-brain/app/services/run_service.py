"""Run service — CRUD for TTS runs."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.enums import RunStatus
from app.models.run import Run
from app.schemas.run import CreateRunRequest, PatchRunRequest
from app.services.http_errors import require_found


async def create(session: AsyncSession, req: CreateRunRequest) -> Run:
    """Create a new TTS run."""
    run = Run(
        id=uuid.uuid4(),
        workflow_id=req.workflow_id,
        story_id=req.story_id,
        voice_id=req.voice_id,
        input_text=req.input_text,
        status=RunStatus.PENDING,
    )
    session.add(run)
    await session.flush()
    return run


async def get(session: AsyncSession, run_id: uuid.UUID) -> Run:
    """Fetch a run by ID; raises 404 if absent."""
    result = await session.execute(
        select(Run)
        .where(Run.id == run_id)
        .options(selectinload(Run.chunks))
    )
    run = result.scalar_one_or_none()
    return require_found(run, f"Run {run_id} not found")


async def list_runs(
    session: AsyncSession,
    limit: int,
    offset: int,
) -> list[Run]:
    """Return runs ordered by creation time (newest first)."""
    result = await session.execute(
        select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def patch(
    session: AsyncSession,
    run_id: uuid.UUID,
    req: PatchRunRequest,
) -> Run:
    """Apply a partial update to a run; raises 404 if absent."""
    run = await get(session, run_id)
    fields = req.model_fields_set
    if "status" in fields and req.status is not None:
        run.status = req.status
    if "final_audio_blob_id" in fields:
        run.final_audio_blob_id = req.final_audio_blob_id
    if "total_duration_sec" in fields:
        run.total_duration_sec = req.total_duration_sec
    if "completed_at" in fields:
        run.completed_at = req.completed_at
    await session.flush()
    return run

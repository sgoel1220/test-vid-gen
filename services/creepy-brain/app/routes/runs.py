"""Run CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.db import DbSession
from app.schemas.run import CreateRunRequest, PatchRunRequest, RunResponse
from app.services import run_service

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("", response_model=RunResponse)
async def create_run(body: CreateRunRequest, session: DbSession) -> RunResponse:
    run = await run_service.create(session, body)
    await session.commit()
    return RunResponse.model_validate(run)


@router.get("", response_model=list[RunResponse])
async def list_runs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[RunResponse]:
    runs = await run_service.list_runs(session, limit=limit, offset=offset)
    return [RunResponse.model_validate(r) for r in runs]


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: uuid.UUID, session: DbSession) -> RunResponse:
    run = await run_service.get(session, run_id)
    return RunResponse.model_validate(run)


@router.patch("/{run_id}", response_model=RunResponse)
async def patch_run(
    run_id: uuid.UUID, body: PatchRunRequest, session: DbSession
) -> RunResponse:
    run = await run_service.patch(session, run_id, body)
    await session.commit()
    return RunResponse.model_validate(run)

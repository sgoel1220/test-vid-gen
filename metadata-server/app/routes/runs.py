"""Run CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from creepy_pasta_protocol.runs import CreateRunRequest, PatchRunRequest, RunDetailDTO, RunSummaryDTO

from app.auth import require_api_key
from app.converters import run_to_detail, run_to_summary
from app.db import DbSession
from app.services import runs as runs_svc

router = APIRouter(prefix="/v1/runs", tags=["runs"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=RunDetailDTO)
async def create_run(body: CreateRunRequest, session: DbSession) -> RunDetailDTO:
    run = await runs_svc.create(session, body)
    detail = await runs_svc.get_detail(session, run.id)
    await session.commit()
    return run_to_detail(detail)


@router.patch("/{run_id}", response_model=RunDetailDTO)
async def patch_run(run_id: uuid.UUID, body: PatchRunRequest, session: DbSession) -> RunDetailDTO:
    await runs_svc.patch(session, run_id, body)
    detail = await runs_svc.get_detail(session, run_id)
    await session.commit()
    return run_to_detail(detail)


@router.get("", response_model=list[RunSummaryDTO])
async def list_runs(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[RunSummaryDTO]:
    runs = await runs_svc.get_summary_list(session, limit=limit, offset=offset)
    return [run_to_summary(r) for r in runs]


@router.get("/{run_id}", response_model=RunDetailDTO)
async def get_run(run_id: uuid.UUID, session: DbSession) -> RunDetailDTO:
    run = await runs_svc.get_detail(session, run_id)
    return run_to_detail(run)

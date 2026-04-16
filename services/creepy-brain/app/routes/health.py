"""Health check endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.db import DbSession
from app.schemas import HealthResponse, ServiceInfo

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
async def readyz(session: DbSession) -> HealthResponse:
    await session.execute(text("SELECT 1"))
    return HealthResponse(status="ok")


@router.get("/", response_model=ServiceInfo)
async def root() -> ServiceInfo:
    return ServiceInfo(service="creepy-brain", version="0.1.0", status="running")

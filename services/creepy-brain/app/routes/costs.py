"""Cost tracking API routes."""

import uuid

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db as _db
from app.services.cost_service import CostService, CostSummary, WorkflowCost

router = APIRouter(prefix="/api/costs", tags=["costs"])


def _get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the initialized DB session maker."""
    maker = _db.async_session_maker
    if maker is None:
        raise RuntimeError("DB not initialized")
    return maker


@router.get("/summary")
async def get_cost_summary() -> CostSummary:
    """Get aggregated cost summary (today, this month, active pods)."""
    session_maker = _get_session_maker()
    async with session_maker() as session:
        return await CostService(session).get_summary()


@router.get("/workflow/{workflow_id}")
async def get_workflow_cost(workflow_id: uuid.UUID) -> WorkflowCost:
    """Get total cost for a specific workflow."""
    session_maker = _get_session_maker()
    async with session_maker() as session:
        svc = CostService(session)
        total = await svc.get_workflow_cost(workflow_id)
    return WorkflowCost(
        workflow_id=str(workflow_id),
        total_cost_cents=total,
        pod_count=0,  # TODO: add pod count query if needed
    )

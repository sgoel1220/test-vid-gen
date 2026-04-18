"""Cost tracking API routes."""

import uuid

from fastapi import APIRouter

from app.db import async_session_maker
from app.services.cost_service import CostService, CostSummary, WorkflowCost

router = APIRouter(prefix="/api/costs", tags=["costs"])


@router.get("/summary")
async def get_cost_summary() -> CostSummary:
    """Get aggregated cost summary (today, this month, active pods)."""
    assert async_session_maker is not None
    async with async_session_maker() as session:
        return await CostService(session).get_summary()


@router.get("/workflow/{workflow_id}")
async def get_workflow_cost(workflow_id: uuid.UUID) -> WorkflowCost:
    """Get total cost for a specific workflow."""
    assert async_session_maker is not None
    async with async_session_maker() as session:
        svc = CostService(session)
        total = await svc.get_workflow_cost(workflow_id)
    return WorkflowCost(
        workflow_id=str(workflow_id),
        total_cost_cents=total,
        pod_count=0,  # TODO: add pod count query if needed
    )

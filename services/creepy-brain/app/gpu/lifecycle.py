"""GPU pod lifecycle helpers shared across workflow steps.

The terminate-and-finalize pattern was previously duplicated in
tts.py, image.py, and cleanup.py.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.gpu.base import GpuProvider
from app.services.cost_service import CostService

log = logging.getLogger(__name__)


async def terminate_and_finalize(
    provider: GpuProvider,
    pod_id: str,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    reason: str = "normal",
) -> int:
    """Terminate a GPU pod and finalize its cost in a single operation.

    Args:
        provider: The GPU provider (e.g. RunPod) instance.
        pod_id: The pod to terminate.
        session_maker: SQLAlchemy async session maker.
        reason: Reason string recorded in the cost record.

    Returns:
        Total cost in cents.

    Raises:
        Any exception from the provider or DB is propagated.
    """
    await provider.terminate_pod(pod_id)
    async with session_maker() as session:
        total_cost = await CostService(session).finalize_cost(pod_id, reason=reason)
    log.info("pod terminated pod_id=%s cost_cents=%d reason=%s", pod_id, total_cost, reason)
    return total_cost

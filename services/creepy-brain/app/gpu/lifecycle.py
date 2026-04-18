"""GPU pod lifecycle helpers shared across workflow steps.

The terminate-and-finalize pattern was previously duplicated in
tts.py, image.py, and cleanup.py.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.gpu.base import GpuPod, GpuPodSpec, GpuProvider
from app.models.enums import GpuProvider as GpuProviderEnum
from app.services.cost_service import CostService

log = logging.getLogger(__name__)


async def create_recorded_pod(
    provider: GpuProvider,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    spec: GpuPodSpec,
    idempotency_key: str,
    workflow_id: uuid.UUID | None,
    label: str,
) -> GpuPod:
    """Create a GPU pod and persist its cost-tracking record."""
    pod = await provider.create_pod(
        spec=spec,
        idempotency_key=idempotency_key,
    )
    log.info("%s pod created pod_id=%s provider=%s", label, pod.id, pod.provider)

    async with session_maker() as session:
        await CostService(session).record_pod(
            pod_id=pod.id,
            provider=GpuProviderEnum(pod.provider),
            workflow_id=workflow_id,
            gpu_type=pod.gpu_type,
            cost_per_hour_cents=pod.cost_per_hour_cents,
        )

    return pod


async def wait_for_recorded_ready(
    provider: GpuProvider,
    session_maker: async_sessionmaker[AsyncSession],
    pod_id: str,
    *,
    timeout_sec: int,
    label: str,
    service_port: int | None = None,
) -> tuple[GpuPod, str]:
    """Wait for a GPU pod to become ready and persist its ready endpoint."""
    pod = await provider.wait_for_ready(
        pod_id,
        timeout_sec=timeout_sec,
        service_port=service_port,
    )
    endpoint_url = pod.endpoint_url
    if endpoint_url is None:
        raise RuntimeError(f"pod {pod.id} ready but has no endpoint_url")

    log.info("%s pod ready endpoint=%s", label, endpoint_url)

    async with session_maker() as session:
        await CostService(session).mark_ready(pod.id, endpoint_url)

    return pod, endpoint_url


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

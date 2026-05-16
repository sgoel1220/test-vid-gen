"""GPU pod lifecycle helpers shared across workflow steps.

The terminate-and-finalize pattern was previously duplicated in
tts.py, image.py, and cleanup.py.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

if TYPE_CHECKING:
    from app.config import GpuTierName

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
    gpu_type_fallbacks: list[str] | None = None,
) -> GpuPod:
    """Create a GPU pod and persist its cost-tracking record.

    If ``gpu_type_fallbacks`` is provided, retries with each fallback GPU type
    in order when the primary type has no available instances.
    """
    from app.gpu.base import NoInstancesAvailableError

    candidates = [spec.gpu_type] + (gpu_type_fallbacks or [])
    last_exc: NoInstancesAvailableError | None = None

    for gpu_type in candidates:
        attempt_spec = spec.model_copy(update={"gpu_type": gpu_type})
        try:
            pod = await provider.create_pod(
                spec=attempt_spec,
                idempotency_key=idempotency_key,
            )
        except NoInstancesAvailableError as exc:
            log.warning(
                "%s no instances available for gpu_type=%s; trying next fallback",
                label,
                gpu_type,
            )
            last_exc = exc
            continue

        log.info(
            "%s pod created pod_id=%s provider=%s gpu_type=%s",
            label,
            pod.id,
            pod.provider,
            gpu_type,
        )

        async with session_maker() as session:
            await CostService(session).record_pod(
                pod_id=pod.id,
                provider=GpuProviderEnum(pod.provider),
                workflow_id=workflow_id,
                gpu_type=pod.gpu_type,
                cost_per_hour_cents=pod.cost_per_hour_cents,
            )

        return pod

    raise last_exc or NoInstancesAvailableError(
        f"No instances available for any GPU type: {candidates}"
    )


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
    terminated = await provider.terminate_pod(pod_id)
    if not terminated:
        raise RuntimeError(
            f"provider.terminate_pod returned False for pod {pod_id}; "
            "cost record not finalized — pod may still be running"
        )
    async with session_maker() as session:
        total_cost = await CostService(session).finalize_cost(pod_id, reason=reason)
    log.info("pod terminated pod_id=%s cost_cents=%d reason=%s", pod_id, total_cost, reason)
    return total_cost


from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator


@asynccontextmanager
async def gpu_pod(
    provider: GpuProvider,
    session_maker: async_sessionmaker[AsyncSession],
    *,
    spec: GpuPodSpec,
    idempotency_key: str,
    workflow_id: uuid.UUID | None,
    label: str,
    gpu_type_fallbacks: list[str] | None = None,
    timeout_sec: int,
    service_port: int | None = None,
) -> AsyncGenerator[tuple[GpuPod, str], None]:
    """Async context manager: create pod → wait ready → yield (pod, endpoint_url) → terminate.

    Terminate-in-finally is always attempted; terminate exceptions are swallowed and logged
    so the original work-body exception (if any) is not masked.

    Usage::

        async with gpu_pod(provider, session_maker, spec=..., ...) as (pod, endpoint_url):
            result = await do_work(endpoint_url)
    """
    pod = await create_recorded_pod(
        provider,
        session_maker,
        spec=spec,
        idempotency_key=idempotency_key,
        workflow_id=workflow_id,
        label=label,
        gpu_type_fallbacks=gpu_type_fallbacks,
    )
    try:
        pod, endpoint_url = await wait_for_recorded_ready(
            provider,
            session_maker,
            pod.id,
            timeout_sec=timeout_sec,
            label=label,
            service_port=service_port,
        )
        yield pod, endpoint_url
    finally:
        try:
            await terminate_and_finalize(provider, pod.id, session_maker)
        except Exception as term_exc:
            log.error("failed to terminate %s pod %s: %s", label, pod.id, term_exc)


@asynccontextmanager
async def workflow_gpu_pod(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    spec: GpuPodSpec,
    idempotency_key: str,
    workflow_id: uuid.UUID | None,
    label: str,
    gpu_tier: "GpuTierName | None" = None,
    service_port: int | None = None,
    pod_ready_timeout_sec: int | None = None,
) -> AsyncGenerator[tuple[GpuPod, str], None]:
    """Like ``gpu_pod`` but pulls provider, fallbacks, and timeout from app settings.

    Eliminates the per-step boilerplate of constructing a provider and
    forwarding ``gpu_type_fallbacks`` / ``pod_ready_timeout_sec`` by hand.

    When *gpu_tier* is provided, fallbacks come from the tier's GPU list
    (skipping the first entry which is already ``spec.gpu_type``).
    Otherwise falls back to the global ``settings.gpu_type_fallbacks``.

    Usage::

        async with workflow_gpu_pod(session_maker, spec=..., ...) as (pod, url):
            result = await do_work(url)
    """
    from app.config import settings  # lazy — avoids gpu → workflows circular dep
    from app.gpu import get_provider_from_settings  # lazy — same reason

    # Determine fallbacks: tier-specific or global
    if gpu_tier is not None:
        tier = settings.gpu_tier(gpu_tier)
        fallbacks = tier.gpu_types[1:]  # skip first (already in spec.gpu_type)
    else:
        fallbacks = settings.gpu_type_fallbacks

    provider = get_provider_from_settings()
    async with gpu_pod(
        provider,
        session_maker,
        spec=spec,
        idempotency_key=idempotency_key,
        workflow_id=workflow_id,
        label=label,
        gpu_type_fallbacks=fallbacks,
        timeout_sec=pod_ready_timeout_sec if pod_ready_timeout_sec is not None else settings.pod_ready_timeout_sec,
        service_port=service_port,
    ) as result:
        yield result

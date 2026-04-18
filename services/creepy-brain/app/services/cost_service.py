"""GPU cost tracking service."""

import uuid
from datetime import date, datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import GpuPodStatus, GpuProvider
from app.models.gpu_pod import GpuPod


class CostSummary(BaseModel):
    """Aggregated cost summary."""

    today_cents: int
    month_cents: int
    active_pod_count: int


class WorkflowCost(BaseModel):
    """Cost for a single workflow."""

    workflow_id: str
    total_cost_cents: int
    pod_count: int


class CostService:
    """Tracks GPU pod costs.

    NOTE: This service commits its own transactions (self-committing).
    This is an intentional deviation from the flush-only convention used
    by other services, because cost mutations must be durable even if the
    caller's outer transaction rolls back (e.g. pod terminated but step fails).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_pod(
        self,
        pod_id: str,
        provider: GpuProvider,
        workflow_id: uuid.UUID | None,
        gpu_type: str | None,
        cost_per_hour_cents: int | None,
        endpoint_url: str | None = None,
        status: GpuPodStatus = GpuPodStatus.CREATING,
    ) -> GpuPod:
        """Insert or update a pod record in the database."""
        existing = await self._session.get(GpuPod, pod_id)
        if existing is not None:
            existing.status = status
            if endpoint_url:
                existing.endpoint_url = endpoint_url
            if cost_per_hour_cents is not None:
                existing.cost_per_hour_cents = cost_per_hour_cents
            await self._session.commit()
            return existing

        pod = GpuPod(
            id=pod_id,
            provider=provider,
            workflow_id=workflow_id,
            status=status,
            gpu_type=gpu_type,
            cost_per_hour_cents=cost_per_hour_cents,
            endpoint_url=endpoint_url,
        )
        self._session.add(pod)
        await self._session.commit()
        return pod

    async def mark_ready(self, pod_id: str, endpoint_url: str | None = None) -> None:
        """Mark a pod as ready and start cost tracking."""
        pod = await self._session.get(GpuPod, pod_id)
        if pod is None:
            return
        pod.status = GpuPodStatus.READY
        pod.ready_at = datetime.now(timezone.utc)
        if endpoint_url:
            pod.endpoint_url = endpoint_url
        await self._session.commit()

    async def finalize_cost(
        self,
        pod_id: str,
        reason: str = "normal",
    ) -> int:
        """Calculate and store final cost when a pod is terminated.

        Returns total cost in cents.
        """
        pod = await self._session.get(GpuPod, pod_id)
        if pod is None:
            return 0

        now = datetime.now(timezone.utc)
        pod.status = GpuPodStatus.TERMINATED
        pod.terminated_at = now
        pod.termination_reason = reason

        if pod.ready_at and pod.cost_per_hour_cents:
            runtime_hours = (now - pod.ready_at).total_seconds() / 3600
            pod.total_cost_cents = int(runtime_hours * pod.cost_per_hour_cents)

        await self._session.commit()
        return pod.total_cost_cents

    async def get_workflow_cost(self, workflow_id: uuid.UUID) -> int:
        """Sum cost of all pods for a workflow."""
        result = await self._session.execute(
            select(func.coalesce(func.sum(GpuPod.total_cost_cents), 0)).where(
                GpuPod.workflow_id == workflow_id
            )
        )
        return int(result.scalar_one())

    async def get_summary(self) -> CostSummary:
        """Get aggregated cost summary (today, month, active pods)."""
        today = date.today()
        month_start = today.replace(day=1)

        today_result = await self._session.execute(
            select(func.coalesce(func.sum(GpuPod.total_cost_cents), 0)).where(
                func.date(GpuPod.created_at) == today
            )
        )
        today_cents = int(today_result.scalar_one())

        month_result = await self._session.execute(
            select(func.coalesce(func.sum(GpuPod.total_cost_cents), 0)).where(
                GpuPod.created_at >= datetime.combine(month_start, datetime.min.time(), timezone.utc)
            )
        )
        month_cents = int(month_result.scalar_one())

        active_result = await self._session.execute(
            select(func.count(GpuPod.id)).where(
                GpuPod.status.notin_([GpuPodStatus.TERMINATED, GpuPodStatus.ERROR])
            )
        )
        active_count = int(active_result.scalar_one())

        return CostSummary(
            today_cents=today_cents,
            month_cents=month_cents,
            active_pod_count=active_count,
        )

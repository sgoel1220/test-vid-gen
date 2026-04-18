"""Recon workflow: find and terminate orphaned GPU pods.

Runs every 5 minutes via CronScheduler. A pod is orphaned if:
1. Running > 2 hours (hard limit regardless of workflow status)
2. Associated workflow is completed/failed/cancelled
3. Running > 30 minutes with no associated workflow
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db as _db
from app.config import settings
from app.engine import StepContext, StepDef, WorkflowDef, engine
from app.gpu import GpuProvider, get_provider
from app.models.enums import GpuPodStatus, WorkflowStatus
from app.models.gpu_pod import GpuPod
from app.models.workflow import Workflow
from app.workflows.types import EmptyModel

log = logging.getLogger(__name__)

_HARD_LIMIT = timedelta(hours=2)
_NO_WORKFLOW_LIMIT = timedelta(minutes=30)
_TERMINAL_STATUSES = {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}

# Cron expression for the scheduler (*/5 * * * * = every 5 minutes)
RECON_CRON = "*/5 * * * *"


class ReconStepOutput(BaseModel):
    """Summary of an orphaned GPU pod sweep."""

    model_config = ConfigDict(extra="forbid")

    db_checked: int = Field(ge=0, description="Number of DB-tracked pods inspected")
    provider_untracked: int = Field(ge=0, description="Number of untracked provider pods found")
    terminated: int = Field(ge=0, description="Number of pods terminated")
    error: str | None = Field(default=None, description="Non-fatal sweep error")


async def _recon_orphaned_pods(
    input: EmptyModel, ctx: StepContext
) -> ReconStepOutput:
    """Find and terminate orphaned GPU pods.

    Two-phase sweep:
    1. DB sweep — check non-terminated pods in our DB against orphan rules.
    2. Provider sweep — list all live pods from RunPod and terminate any that
       are not tracked in our DB or have no linked workflow.
    """
    await _ensure_db()
    session_maker = _db.async_session_maker
    if session_maker is None:
        return ReconStepOutput(
            db_checked=0,
            provider_untracked=0,
            terminated=0,
            error="db_not_initialized",
        )

    now = datetime.now(timezone.utc)
    provider = get_provider(settings.runpod_api_key)
    terminated = 0

    # ── Phase 1: DB sweep ───────────────────────────────────────────────
    async with session_maker() as session:
        result = await session.execute(
            select(GpuPod).where(
                GpuPod.status.notin_([GpuPodStatus.TERMINATED, GpuPodStatus.ERROR]),
            )
        )
        db_pods = list(result.scalars().all())

    db_pod_ids: set[str] = set()
    for pod in db_pods:
        db_pod_ids.add(pod.id)
        reason = _check_orphaned(pod, now)

        if reason is None and pod.workflow_id is not None:
            async with session_maker() as session:
                wf_result = await session.execute(
                    select(Workflow.status).where(Workflow.id == pod.workflow_id)
                )
                wf_status = wf_result.scalar_one_or_none()
                if wf_status is not None and wf_status in _TERMINAL_STATUSES:
                    reason = f"workflow_{wf_status.value}"

        if reason is None:
            continue

        terminated += await _terminate_pod(provider, pod.id, reason, now, session_maker)

    # ── Phase 2: Provider sweep ─────────────────────────────────────────
    try:
        live_pods = await provider.list_active_pods()
    except Exception:
        log.exception("recon: failed to list active pods from provider")
        live_pods = []

    untracked = 0
    for live_pod in live_pods:
        if live_pod.id in db_pod_ids:
            continue

        async with session_maker() as session:
            db_result = await session.execute(
                select(GpuPod).where(GpuPod.id == live_pod.id)
            )
            db_pod = db_result.scalar_one_or_none()

        if db_pod is not None and db_pod.status in (GpuPodStatus.TERMINATED, GpuPodStatus.ERROR):
            reason = "provider_still_running_after_db_terminated"
        elif db_pod is None:
            age_min = (
                (now - live_pod.created_at).total_seconds() / 60
                if live_pod.created_at is not None
                else float("inf")
            )
            if age_min < 10:
                continue
            reason = f"untracked_pod (age={age_min:.0f}m)"
        else:
            continue

        untracked += 1
        terminated += await _terminate_pod(provider, live_pod.id, reason, now, session_maker)

    log.info(
        "recon: db_checked=%d provider_untracked=%d terminated=%d",
        len(db_pods),
        untracked,
        terminated,
    )
    return ReconStepOutput(
        db_checked=len(db_pods),
        provider_untracked=untracked,
        terminated=terminated,
    )


async def _terminate_pod(
    provider: GpuProvider,
    pod_id: str,
    reason: str,
    now: datetime,
    session_maker: async_sessionmaker[AsyncSession],
) -> int:
    """Terminate a pod and update DB. Returns 1 on success, 0 on failure."""
    log.warning("recon: terminating orphaned pod", extra={"pod_id": pod_id, "reason": reason})
    try:
        await provider.terminate_pod(pod_id)
    except Exception:
        log.exception("recon: failed to terminate pod %s", pod_id)
        return 0

    async with session_maker() as session:
        db_result = await session.execute(
            select(GpuPod).where(GpuPod.id == pod_id)
        )
        db_pod = db_result.scalar_one_or_none()
        if db_pod is not None:
            db_pod.status = GpuPodStatus.TERMINATED
            db_pod.terminated_at = now
            db_pod.termination_reason = f"recon: {reason}"
            await session.commit()

    return 1


def _check_orphaned(pod: GpuPod, now: datetime) -> str | None:
    """Return a reason string if the pod is orphaned, else None."""
    age = now - pod.created_at
    if age > _HARD_LIMIT:
        hours = age.total_seconds() / 3600
        return f"hard_limit_exceeded (age={hours:.1f}h)"

    if pod.workflow_id is None and age > _NO_WORKFLOW_LIMIT:
        minutes = age.total_seconds() / 60
        return f"no_workflow (age={minutes:.0f}m)"

    return None


_db_init_lock = __import__("asyncio").Lock()


async def _ensure_db() -> None:
    """Initialize the DB engine if not already done (idempotent)."""
    async with _db_init_lock:
        if _db.async_session_maker is None:
            await _db.init_db()


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

recon_workflow_def = WorkflowDef(
    name="ReconOrphanedPods",
    steps=[
        StepDef(
            name="recon_orphaned_pods",
            fn=_recon_orphaned_pods,
            timeout_sec=120,
        ),
    ],
)

engine.register(recon_workflow_def)

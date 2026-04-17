"""on_failure cleanup step: terminate any active GPU pods for the workflow."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from hatchet_sdk import Context
from sqlalchemy import select

import app.db as _db
from app.config import settings
from app.gpu import get_provider
from app.models.enums import GpuPodStatus
from app.models.gpu_pod import GpuPod
from app.models.schemas import WorkflowInputSchema
from app.services.workflow_service import get_optional_workflow_id

log = logging.getLogger(__name__)

# Serializes lazy DB initialization so concurrent step starts don't race.
_db_init_lock: asyncio.Lock = asyncio.Lock()


async def _ensure_db() -> None:
    """Initialize the DB engine if not already done (idempotent)."""
    async with _db_init_lock:
        if _db.async_session_maker is None:
            await _db.init_db()


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Terminate active GPU pods when the ContentPipeline workflow fails.

    This is registered as the ``on_failure`` hook for ContentPipeline, so Hatchet
    calls it automatically whenever any step fails.  It is safe to call even when
    no pod exists — it returns early with ``skipped=True`` in that case.

    Returns a summary dict that Hatchet serialises; callers do not rely on it.
    """
    await _ensure_db()
    session_maker = _db.async_session_maker
    assert session_maker is not None  # guaranteed by _ensure_db

    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        log.warning(
            "cleanup_gpu_pod: could not parse workflow_run_id=%s", ctx.workflow_run_id
        )
        return {"skipped": True, "reason": "unparseable_workflow_run_id"}

    # Load active (non-terminated, non-errored) pods linked to this workflow.
    async with session_maker() as session:
        result = await session.execute(
            select(GpuPod).where(
                GpuPod.workflow_id == workflow_id,
                GpuPod.status.notin_([GpuPodStatus.TERMINATED, GpuPodStatus.ERROR]),
            )
        )
        pods = list(result.scalars().all())

    if not pods:
        log.info("cleanup_gpu_pod: no active pods for workflow %s", workflow_id)
        return {"skipped": True, "reason": "no_active_pods"}

    provider = get_provider(settings.runpod_api_key)
    pod_results: list[dict[str, object]] = []

    for pod in pods:
        pod_id: str = pod.id
        try:
            terminated = await provider.terminate_pod(pod_id)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup, must not re-raise
            log.error(
                "cleanup_gpu_pod: failed to terminate pod %s: %s", pod_id, exc
            )
            pod_results.append({"pod_id": pod_id, "error": str(exc)})
            continue

        if terminated:
            log.info(
                "cleanup_gpu_pod: terminated pod %s workflow=%s", pod_id, workflow_id
            )
            # Record confirmed termination in the database.
            async with session_maker() as session:
                db_result = await session.execute(
                    select(GpuPod).where(GpuPod.id == pod_id)
                )
                db_pod = db_result.scalar_one_or_none()
                if db_pod is not None:
                    db_pod.status = GpuPodStatus.TERMINATED
                    db_pod.terminated_at = datetime.now(timezone.utc)
                    db_pod.termination_reason = "workflow_failure"
                    await session.commit()
        else:
            # Provider returned False — pod may still be running; leave DB row active
            # so the recon cron job (bead lm4) can detect and terminate it.
            log.warning(
                "cleanup_gpu_pod: provider returned False for pod %s workflow=%s",
                pod_id,
                workflow_id,
            )

        pod_results.append({"pod_id": pod_id, "terminated": terminated})

    return {"pod_count": len(pods), "results": pod_results}

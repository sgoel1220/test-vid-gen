"""on_failure cleanup step: terminate any active GPU pods for the workflow."""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from hatchet_sdk import Context
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.config import settings
from app.gpu import get_provider
from app.gpu.lifecycle import terminate_and_finalize
from app.models.enums import GpuPodStatus
from app.models.gpu_pod import GpuPod
from app.models.schemas import WorkflowInputSchema
from app.services.workflow_service import get_optional_workflow_id

from app.workflows.db_helpers import ensure_db, get_session_maker

log = logging.getLogger(__name__)


class PodCleanupResult(BaseModel):
    """Result for a single pod termination attempt."""

    model_config = ConfigDict(extra="forbid")

    pod_id: str
    terminated: bool = False
    error: str | None = None


class CleanupStepOutput(BaseModel):
    """Output when cleanup actually runs."""

    model_config = ConfigDict(extra="forbid")

    pod_count: int = Field(ge=0, description="Number of active pods found")
    results: list[PodCleanupResult] = Field(description="Per-pod termination results")


class SkippedStepOutput(BaseModel):
    """Output when cleanup is skipped."""

    model_config = ConfigDict(extra="forbid")

    skipped: bool = True
    reason: str


async def execute(input: WorkflowInputSchema, ctx: Context) -> dict[str, object]:
    """Terminate active GPU pods when the ContentPipeline workflow fails.

    This is registered as the ``on_failure`` hook for ContentPipeline, so Hatchet
    calls it automatically whenever any step fails.  It is safe to call even when
    no pod exists — it returns early with ``skipped=True`` in that case.

    Returns a summary dict that Hatchet serialises; callers do not rely on it.
    """
    await ensure_db()
    session_maker = get_session_maker()

    workflow_id: Optional[uuid.UUID] = get_optional_workflow_id(ctx.workflow_run_id)
    if workflow_id is None:
        log.warning(
            "cleanup_gpu_pod: could not parse workflow_run_id=%s", ctx.workflow_run_id
        )
        return SkippedStepOutput(reason="unparseable_workflow_run_id").model_dump()

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
        return SkippedStepOutput(reason="no_active_pods").model_dump()

    provider = get_provider(settings.runpod_api_key)
    pod_results: list[PodCleanupResult] = []

    for pod in pods:
        pod_id: str = pod.id
        try:
            await terminate_and_finalize(
                provider, pod_id, session_maker, reason="workflow_failure"
            )
            pod_results.append(PodCleanupResult(pod_id=pod_id, terminated=True))
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup, must not re-raise
            log.error(
                "cleanup_gpu_pod: failed to terminate pod %s: %s", pod_id, exc
            )
            pod_results.append(PodCleanupResult(pod_id=pod_id, error=str(exc)))

    return CleanupStepOutput(pod_count=len(pods), results=pod_results).model_dump()

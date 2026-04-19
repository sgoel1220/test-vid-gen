"""Workflow pause/cancel and GPU resource cleanup."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from sqlalchemy import select

from .retry_resume_controller import SetWorkflowStatus
from .task_supervisor import CancelTaskCallback

log = logging.getLogger(__name__)

SessionMakerGetter = Callable[[], Any | None]

if TYPE_CHECKING:
    GpuPodStatus: TypeAlias = Any
    WorkflowStatus: TypeAlias = Any
else:
    _enums_module = import_module("app.models.enums")
    GpuPodStatus = getattr(_enums_module, "GpuPodStatus")
    WorkflowStatus = getattr(_enums_module, "WorkflowStatus")


def _gpu_pod_model() -> Any:
    return getattr(import_module("app.models.gpu_pod"), "GpuPod")


class TerminateGpuPods(Protocol):
    """Callable shape for GPU pod termination."""

    async def __call__(self, workflow_id: uuid.UUID) -> None:
        """Terminate workflow GPU pods."""


class WorkflowResourceController:
    """Handle pause/cancel side effects and GPU cleanup."""

    def __init__(self, session_maker_getter: SessionMakerGetter) -> None:
        self._session_maker_getter = session_maker_getter

    async def pause(
        self,
        workflow_run_id: str,
        *,
        cancel_task: CancelTaskCallback,
        terminate_gpu_pods: TerminateGpuPods,
        set_workflow_status: SetWorkflowStatus,
    ) -> None:
        """Pause a running workflow."""
        run_id = workflow_run_id
        await cancel_task(run_id, mark_cancelled_in_db=False)

        try:
            await terminate_gpu_pods(uuid.UUID(run_id))
        except (ValueError, Exception) as exc:
            log.error("engine: pause GPU pod termination failed for %s: %s", run_id, exc)

        try:
            await set_workflow_status(uuid.UUID(run_id), WorkflowStatus.PAUSED)
        except Exception as exc:
            log.error("engine: pause failed to set PAUSED status for %s: %s", run_id, exc)

        log.info("engine: paused workflow %s", run_id)

    async def cancel(
        self,
        workflow_run_id: str,
        *,
        cancel_task: CancelTaskCallback,
        terminate_gpu_pods: TerminateGpuPods,
    ) -> None:
        """Cancel a running workflow."""
        await cancel_task(workflow_run_id, mark_cancelled_in_db=True)
        try:
            await terminate_gpu_pods(uuid.UUID(workflow_run_id))
        except (ValueError, Exception) as exc:
            log.error("engine: cancel GPU pod termination failed for %s: %s", workflow_run_id, exc)

    async def terminate_gpu_pods(self, workflow_id: uuid.UUID) -> None:
        """Best-effort: terminate active GPU pods for this workflow."""
        session_maker = self._session_maker_getter()
        if session_maker is None:
            return
        try:
            GpuPod = _gpu_pod_model()
            settings = getattr(import_module("app.config"), "settings")
            get_provider = getattr(import_module("app.gpu"), "get_provider")
            terminate_and_finalize = getattr(
                import_module("app.gpu.lifecycle"),
                "terminate_and_finalize",
            )

            async with session_maker() as session:
                result = await session.execute(
                    select(GpuPod).where(
                        GpuPod.workflow_id == workflow_id,
                        GpuPod.status.notin_(
                            [GpuPodStatus.TERMINATED, GpuPodStatus.ERROR]
                        ),
                    )
                )
                pods = list(result.scalars().all())

            if not pods:
                return

            provider = get_provider(settings.runpod_api_key)
            for pod in pods:
                try:
                    await terminate_and_finalize(
                        provider,
                        pod.id,
                        session_maker,
                        reason="workflow_cancelled",
                    )
                except Exception as exc:
                    log.error("engine: failed to terminate pod %s: %s", pod.id, exc)
        except Exception as exc:
            log.error("engine: _terminate_gpu_pods error: %s", exc)

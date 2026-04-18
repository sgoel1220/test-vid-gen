"""Abstract base classes and data structures for GPU providers.

Status lifecycle
----------------
CREATING  – pod accepted by provider, not yet booted
RUNNING   – pod is running per the provider API (desiredStatus == RUNNING)
READY     – RUNNING + health probe passed; only assigned by wait_for_ready()
TERMINATED – pod has exited or been terminated
ERROR     – provider reported an error state
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.models.enums import GpuPodStatus


@dataclass
class GpuPodSpec:
    """Spec for creating a GPU pod. Defaults loaded from config."""

    gpu_type: str
    image: str
    disk_size_gb: int
    volume_gb: int
    ports: list[int]
    cloud_type: str = "COMMUNITY"  # COMMUNITY or SECURE
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls) -> "GpuPodSpec":
        """Create spec with defaults from app config."""
        from app.config import settings

        return cls(
            gpu_type=settings.gpu_type,
            image=settings.gpu_image,
            disk_size_gb=settings.gpu_container_disk_gb,
            volume_gb=settings.gpu_volume_gb,
            ports=[settings.gpu_port],
            cloud_type=settings.gpu_cloud_type,
        )


@dataclass
class GpuPod:
    id: str
    provider: str
    status: GpuPodStatus
    endpoint_url: str | None
    gpu_type: str | None
    cost_per_hour_cents: int | None
    # None when the provider API does not expose creation time (e.g. via list_active_pods)
    created_at: datetime | None


class GpuProvider(ABC):
    @abstractmethod
    async def create_pod(
        self,
        spec: GpuPodSpec,
        idempotency_key: str,
    ) -> GpuPod:
        """Create a new GPU pod. Idempotency key ensures same pod returned if called twice."""

    @abstractmethod
    async def get_pod(
        self, pod_id: str, service_port: int | None = None
    ) -> GpuPod | None:
        """Get pod status by ID.

        Args:
            pod_id: The pod ID to get.
            service_port: The service port for endpoint URL construction.
        """

    @abstractmethod
    async def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a pod. Returns True if terminated."""

    @abstractmethod
    async def wait_for_ready(
        self,
        pod_id: str,
        timeout_sec: int = 720,
        service_port: int | None = None,
    ) -> GpuPod:
        """Wait for pod to be ready (health check passes).

        Args:
            pod_id: The pod ID to wait for.
            timeout_sec: Maximum time to wait for the pod to become ready.
            service_port: The service port for endpoint URL construction.
        """

    @abstractmethod
    async def list_active_pods(self) -> list[GpuPod]:
        """List all active (non-terminated) pods. Used for recon."""

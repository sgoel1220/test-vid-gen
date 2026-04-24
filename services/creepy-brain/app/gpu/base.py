"""Abstract base classes and data structures for GPU providers.

Status lifecycle
----------------
CREATING  – pod accepted by provider, not yet booted
RUNNING   – pod is running per the provider API (desiredStatus == RUNNING)
READY     – RUNNING + health probe passed; only assigned by wait_for_ready()
TERMINATED – pod has exited or been terminated
ERROR     – provider reported an error state
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GpuPodStatus


class ImagePullStuckError(RuntimeError):
    """Raised when a pod's image pull has been stuck for too long."""


class NoInstancesAvailableError(RuntimeError):
    """Raised when no GPU instances of the requested type are available."""


class GpuPodSpec(BaseModel):
    """Spec for creating a GPU pod. Defaults loaded from config."""

    model_config = ConfigDict(extra="forbid")

    gpu_type: str
    image: str
    disk_size_gb: int
    volume_gb: int
    ports: list[int]
    cloud_type: str = "COMMUNITY"  # COMMUNITY or SECURE
    env: dict[str, str] = Field(default_factory=dict)
    gpu_count: int = 1
    min_download: int = 500  # Mbps; 0 = no filter
    min_upload: int = 500  # Mbps; 0 = no filter

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


class GpuPod(BaseModel):
    """Provider-agnostic GPU pod state."""

    model_config = ConfigDict(extra="forbid")

    id: str
    provider: str
    status: GpuPodStatus
    endpoint_url: str | None
    gpu_type: str | None
    cost_per_hour_cents: int | None
    # None when the provider API does not expose creation time (e.g. via list_active_pods)
    created_at: datetime | None


class GpuProvider(Protocol):
    async def create_pod(self, spec: GpuPodSpec, idempotency_key: str) -> GpuPod: ...
    async def get_pod(self, pod_id: str, service_port: int | None = None) -> GpuPod | None: ...
    async def resume_pod(self, pod_id: str, gpu_count: int = 1, service_port: int | None = None) -> GpuPod: ...
    async def terminate_pod(self, pod_id: str) -> bool: ...
    async def wait_for_ready(self, pod_id: str, timeout_sec: int = 720, service_port: int | None = None) -> GpuPod: ...
    async def list_active_pods(self) -> list[GpuPod]: ...

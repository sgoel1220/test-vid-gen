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
from enum import Enum


class PodStatus(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    READY = "ready"
    TERMINATED = "terminated"
    ERROR = "error"


@dataclass
class GpuPodSpec:
    gpu_type: str = "RTX 4090"
    image: str = "ghcr.io/sgoel1220/tts-server:main"
    disk_size_gb: int = 25
    ports: list[int] = field(default_factory=lambda: [8005])
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class GpuPod:
    id: str
    provider: str
    status: PodStatus
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
    async def get_pod(self, pod_id: str) -> GpuPod | None:
        """Get pod status by ID."""

    @abstractmethod
    async def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a pod. Returns True if terminated."""

    @abstractmethod
    async def wait_for_ready(
        self,
        pod_id: str,
        timeout_sec: int = 300,
    ) -> GpuPod:
        """Wait for pod to be ready (health check passes)."""

    @abstractmethod
    async def list_active_pods(self) -> list[GpuPod]:
        """List all active (non-terminated) pods. Used for recon."""

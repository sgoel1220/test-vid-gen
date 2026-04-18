"""RunPod GPU provider implementation using the official RunPod SDK."""

import asyncio
from datetime import datetime, timezone
from typing import cast

import httpx
import runpod

from app.models.enums import GpuProvider as GpuProviderName

from .base import GpuPod, GpuPodSpec, GpuProvider, PodStatus


class RunPodProvider(GpuProvider):
    """GPU provider backed by the RunPod SDK."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        runpod.api_key = api_key

    def _parse_pod(self, raw: dict[str, object], service_port: int | None = None) -> GpuPod:
        """Parse a raw RunPod API dict into a GpuPod."""
        pod_id = str(raw["id"])
        desired = str(raw.get("desiredStatus", ""))

        if desired == "RUNNING":
            status = PodStatus.RUNNING
        elif desired in ("EXITED", "TERMINATED"):
            status = PodStatus.TERMINATED
        else:
            status = PodStatus.CREATING

        # Build endpoint URL using RunPod proxy format
        endpoint_url: str | None = None
        runtime = raw.get("runtime")
        if isinstance(runtime, dict) and runtime.get("ports"):
            # Use the RunPod proxy URL format
            if service_port:
                endpoint_url = f"https://{pod_id}-{service_port}.proxy.runpod.net"
            else:
                endpoint_url = f"https://{pod_id}-8005.proxy.runpod.net"

        machine = raw.get("machine") or {}
        if isinstance(machine, dict):
            gpu_type = str(machine.get("gpuDisplayName", "")) or None
            cost_raw = machine.get("costPerHr")
            cost_cents = int(float(cost_raw) * 100) if cost_raw else None
        else:
            gpu_type = None
            cost_cents = None

        created_at: datetime | None = None
        raw_created_at = raw.get("createdAt")
        if isinstance(raw_created_at, str):
            try:
                created_at = datetime.fromisoformat(raw_created_at)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        return GpuPod(
            id=pod_id,
            provider=GpuProviderName.RUNPOD,
            status=status,
            endpoint_url=endpoint_url,
            gpu_type=gpu_type,
            cost_per_hour_cents=cost_cents,
            created_at=created_at,
        )

    async def create_pod(self, spec: GpuPodSpec, idempotency_key: str) -> GpuPod:
        """Create a new GPU pod using the RunPod SDK."""
        service_port = spec.ports[0] if spec.ports else 8005

        # Check for existing pod with same name
        existing = await self._find_pod_by_name(idempotency_key, service_port)
        if existing and existing.status != PodStatus.TERMINATED:
            return existing

        ports_str = ",".join(f"{p}/http" for p in spec.ports)
        env_dict = spec.env or {}

        def _create() -> dict[str, object]:
            return cast(
                dict[str, object],
                runpod.create_pod(
                    name=idempotency_key,
                    image_name=spec.image,
                    gpu_type_id=spec.gpu_type,
                    cloud_type=spec.cloud_type,
                    container_disk_in_gb=spec.disk_size_gb,
                    volume_in_gb=spec.volume_gb,
                    ports=ports_str,
                    env=env_dict,
                ),
            )

        try:
            raw = await asyncio.to_thread(_create)
            return self._parse_pod(raw, service_port)
        except Exception:
            # On error, check if pod was created by concurrent call
            recovered = await self._find_pod_by_name(idempotency_key, service_port)
            if recovered and recovered.status != PodStatus.TERMINATED:
                return recovered
            raise

    async def get_pod(
        self, pod_id: str, service_port: int | None = None
    ) -> GpuPod | None:
        """Get pod status by ID."""

        def _get() -> dict[str, object]:
            return cast(dict[str, object], runpod.get_pod(pod_id))

        try:
            raw = await asyncio.to_thread(_get)
            if not raw:
                return None
            return self._parse_pod(raw, service_port)
        except Exception:
            return None

    async def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a pod."""

        def _terminate() -> object:
            return runpod.terminate_pod(pod_id)

        try:
            await asyncio.to_thread(_terminate)
            return True
        except Exception:
            return False

    async def wait_for_ready(
        self, pod_id: str, timeout_sec: int = 720, service_port: int | None = None
    ) -> GpuPod:
        """Wait for pod to be ready (health check passes).

        Args:
            pod_id: The pod ID to wait for.
            timeout_sec: Maximum time to wait for the pod to become ready.
            service_port: The service port to use for endpoint URL construction.
                If not specified, defaults to 8005 (TTS server). Use 8006 for image server.
        """
        deadline = asyncio.get_event_loop().time() + timeout_sec

        while asyncio.get_event_loop().time() < deadline:
            pod = await self.get_pod(pod_id, service_port)
            if pod and pod.endpoint_url:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        r = await client.get(f"{pod.endpoint_url}/health")
                        if r.status_code == 200:
                            pod.status = PodStatus.READY
                            return pod
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
            await asyncio.sleep(10)

        raise TimeoutError(f"Pod {pod_id} did not become ready within {timeout_sec}s")

    async def list_active_pods(self) -> list[GpuPod]:
        """List all active (non-terminated) pods."""

        def _list() -> list[dict[str, object]]:
            return cast(list[dict[str, object]], runpod.get_pods())

        raw_pods = await asyncio.to_thread(_list)
        result: list[GpuPod] = []
        for raw in raw_pods:
            pod = self._parse_pod(raw)
            if pod.status != PodStatus.TERMINATED:
                result.append(pod)
        return result

    async def _find_pod_by_name(
        self, name: str, service_port: int | None = None
    ) -> GpuPod | None:
        """Find a pod by name using a single get_pods() call."""

        def _list() -> list[dict[str, object]]:
            return cast(list[dict[str, object]], runpod.get_pods())

        raw_pods = await asyncio.to_thread(_list)
        for raw in raw_pods:
            if raw.get("name") == name:
                pod = self._parse_pod(raw, service_port)
                if pod.status != PodStatus.TERMINATED:
                    return pod
        return None

    async def aclose(self) -> None:
        """No cleanup needed for SDK-based provider."""
        pass

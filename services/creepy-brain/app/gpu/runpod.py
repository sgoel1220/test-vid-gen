"""RunPod GPU provider implementation using the official RunPod SDK."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import cast

import httpx
import runpod

from app.models.enums import GpuPodStatus

from .base import GpuPod, GpuPodSpec, GpuProvider

log = logging.getLogger(__name__)


def _as_raw_pod(raw: object) -> dict[str, object]:
    """Validate a RunPod SDK pod payload."""
    if not isinstance(raw, dict):
        raise TypeError(f"Expected RunPod pod payload to be a dict, got {type(raw).__name__}")
    return cast(dict[str, object], raw)


def _as_raw_pod_list(raw: object) -> list[dict[str, object]]:
    """Validate a RunPod SDK pod list payload."""
    if not isinstance(raw, list):
        raise TypeError(f"Expected RunPod pod list to be a list, got {type(raw).__name__}")
    pods: list[dict[str, object]] = []
    for item in raw:
        pods.append(_as_raw_pod(item))
    return pods


def _port_endpoint(port: dict[str, object]) -> str | None:
    """Build an HTTP endpoint from a RunPod runtime port record.

    Prefers public IP if available; otherwise returns None (caller
    should fall back to the proxy URL pattern).
    """
    if port.get("isIpPublic") is not True:
        return None

    ip = port.get("ip")
    public_port = port.get("publicPort")
    if not isinstance(ip, str) or public_port is None:
        return None

    try:
        public_port_int = int(str(public_port))
    except ValueError:
        return None

    return f"http://{ip}:{public_port_int}"


def _private_port(port: dict[str, object]) -> int | None:
    """Extract the private service port from a RunPod runtime port record."""
    private_port = port.get("privatePort")
    if private_port is None:
        return None
    try:
        return int(str(private_port))
    except ValueError:
        return None


def _select_endpoint(
    runtime: object,
    service_port: int | None,
    pod_id: str | None = None,
) -> str | None:
    """Select the externally reachable endpoint for a pod service.

    Tries public IP first.  Falls back to RunPod proxy URL
    (https://{pod_id}-{port}.proxy.runpod.net) when no public IP is
    available (common on community cloud / spot instances).
    """
    if not isinstance(runtime, dict):
        if pod_id and service_port:
            return f"https://{pod_id}-{service_port}.proxy.runpod.net"
        return None

    raw_ports = runtime.get("ports")
    if not isinstance(raw_ports, list):
        if pod_id and service_port:
            return f"https://{pod_id}-{service_port}.proxy.runpod.net"
        return None

    ports = [_as_raw_pod(raw_port) for raw_port in raw_ports]

    # Try public IP first.
    if service_port is not None:
        for port in ports:
            if _private_port(port) == service_port:
                endpoint = _port_endpoint(port)
                if endpoint is not None:
                    return endpoint
    else:
        for port in ports:
            endpoint = _port_endpoint(port)
            if endpoint is not None:
                return endpoint

    # Fallback: RunPod proxy URL (requires service_port).
    if pod_id and service_port:
        return f"https://{pod_id}-{service_port}.proxy.runpod.net"

    return None


class RunPodProvider(GpuProvider):
    """GPU provider backed by the RunPod SDK."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        runpod.api_key = api_key

    def _parse_pod(self, raw: dict[str, object], service_port: int | None = None) -> GpuPod:
        """Parse a raw RunPod API dict into a GpuPod.

        Endpoint URL is constructed from public IP/port pairs:
        - If service_port given, finds the entry where privatePort matches and isIpPublic is True.
        - If service_port is None, uses the first publicly exposed port.
        - Returns None endpoint if no matching public port is found.
        """
        pod_id = str(raw["id"])
        desired = str(raw.get("desiredStatus", ""))

        if desired == "RUNNING":
            status = GpuPodStatus.RUNNING
        elif desired == "EXITED":
            status = GpuPodStatus.STOPPED
        elif desired == "TERMINATED":
            status = GpuPodStatus.TERMINATED
        else:
            status = GpuPodStatus.CREATING

        endpoint_url = _select_endpoint(raw.get("runtime"), service_port, pod_id=pod_id)

        machine = raw.get("machine") or {}
        if not isinstance(machine, dict):
            raise TypeError("Expected 'machine' to be a dict")
        if machine:
            gpu_type = str(machine.get("gpuDisplayName", "")) or None
            cost_raw = machine.get("costPerHr") or machine.get("costPerGpu")
            cost_cents = int(float(cost_raw) * 100) if cost_raw else None
        else:
            raise TypeError(
                f"Expected 'machine' to be a dict or None, got {type(machine).__name__!r}"
            )

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
        if existing:
            if existing.status in (GpuPodStatus.RUNNING, GpuPodStatus.CREATING, GpuPodStatus.READY):
                return existing
            if existing.status == GpuPodStatus.STOPPED:
                log.info("Resuming stopped pod %s (name=%s)", existing.id, idempotency_key)
                return await self.resume_pod(existing.id, spec.gpu_count, service_port)

        ports_str = ",".join(f"{p}/http" for p in spec.ports)
        env_dict = spec.env or {}

        def _create() -> object:
            kwargs: dict[str, object] = dict(
                name=idempotency_key,
                image_name=spec.image,
                gpu_type_id=spec.gpu_type,
                cloud_type=spec.cloud_type,
                container_disk_in_gb=spec.disk_size_gb,
                volume_in_gb=spec.volume_gb,
                ports=ports_str,
                env=env_dict,
            )
            if spec.min_download > 0:
                kwargs["min_download"] = spec.min_download
            if spec.min_upload > 0:
                kwargs["min_upload"] = spec.min_upload
            return runpod.create_pod(**kwargs)

        try:
            raw = _as_raw_pod(await asyncio.to_thread(_create))
            pod_id = str(raw["id"])
            # create_pod returns sparse data; fetch full pod info
            full_pod = await self.get_pod(pod_id, service_port)
            if full_pod:
                return full_pod
            # Fallback to parsing sparse response if get_pod fails
            return self._parse_pod(raw, service_port)
        except Exception:
            # On error, check if pod was created by concurrent call
            recovered = await self._find_pod_by_name(idempotency_key, service_port)
            if recovered and recovered.status not in (GpuPodStatus.TERMINATED, GpuPodStatus.STOPPED):
                return recovered
            raise

    async def get_pod(self, pod_id: str, service_port: int | None = None) -> GpuPod | None:
        """Get pod status by ID."""

        def _get() -> object:
            return runpod.get_pod(pod_id)

        try:
            raw = await asyncio.to_thread(_get)
            if not raw:
                return None
            return self._parse_pod(_as_raw_pod(raw), service_port)
        except Exception:
            log.exception("runpod get_pod failed pod_id=%s", pod_id)
            return None

    async def resume_pod(
        self, pod_id: str, gpu_count: int = 1, service_port: int | None = None
    ) -> GpuPod:
        """Resume a stopped pod via the RunPod SDK."""

        def _resume() -> object:
            return runpod.resume_pod(pod_id, gpu_count=gpu_count)

        await asyncio.to_thread(_resume)
        # resume_pod returns sparse data; fetch full pod info
        full_pod = await self.get_pod(pod_id, service_port)
        if full_pod:
            return full_pod
        msg = f"Pod {pod_id} not found after resume"
        raise RuntimeError(msg)

    async def terminate_pod(self, pod_id: str) -> bool:
        """Terminate a pod."""

        def _terminate() -> object:
            return runpod.terminate_pod(pod_id)

        try:
            await asyncio.to_thread(_terminate)
            return True
        except Exception:
            log.exception("runpod terminate_pod failed pod_id=%s", pod_id)
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
                            pod.status = GpuPodStatus.READY
                            return pod
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
            await asyncio.sleep(3)

        raise TimeoutError(f"Pod {pod_id} did not become ready within {timeout_sec}s")

    async def list_active_pods(self) -> list[GpuPod]:
        """List all active (non-terminated) pods."""

        def _list() -> object:
            return runpod.get_pods()

        raw_pods = _as_raw_pod_list(await asyncio.to_thread(_list))
        result: list[GpuPod] = []
        for raw in raw_pods:
            pod = self._parse_pod(raw)
            if pod.status != GpuPodStatus.TERMINATED:
                result.append(pod)
        return result

    async def _find_pod_by_name(self, name: str, service_port: int | None = None) -> GpuPod | None:
        """Find a pod by name using a single get_pods() call."""

        def _list() -> object:
            return runpod.get_pods()

        raw_pods = _as_raw_pod_list(await asyncio.to_thread(_list))
        for raw in raw_pods:
            if raw.get("name") == name:
                pod = self._parse_pod(raw, service_port)
                if pod.status != GpuPodStatus.TERMINATED:
                    return pod
        return None

    async def aclose(self) -> None:
        """No cleanup needed for SDK-based provider."""
        pass

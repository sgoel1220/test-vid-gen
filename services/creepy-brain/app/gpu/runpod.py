"""RunPod GPU provider implementation using the official RunPod SDK."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import cast

import httpx
import runpod

from app.models.enums import GpuPodStatus, GpuProvider as GpuProviderName

from .base import GpuPod, GpuPodSpec

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


class RunPodProvider:
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
        gpu_type = (str(machine.get("gpuDisplayName", "")) or None) if machine else None
        cost_raw = (machine.get("costPerHr") or machine.get("costPerGpu")) if machine else None
        cost_cents = int(float(cost_raw) * 100) if cost_raw else None

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

    async def create_pod(
        self,
        spec: GpuPodSpec,
        idempotency_key: str,
        pull_stuck_timeout_sec: int = 480,
    ) -> GpuPod:
        """Create a new GPU pod using the RunPod SDK."""
        from .base import ImagePullStuckError, NoInstancesAvailableError

        service_port = spec.ports[0] if spec.ports else 8005

        # Check for existing pod with same name
        existing = await self._find_pod_by_name(idempotency_key, service_port)
        if existing:
            if existing.status in (GpuPodStatus.RUNNING, GpuPodStatus.READY):
                return existing
            if existing.status == GpuPodStatus.STOPPED:
                log.info("Resuming stopped pod %s (name=%s)", existing.id, idempotency_key)
                return await self.resume_pod(existing.id, spec.gpu_count, service_port)
            if existing.status == GpuPodStatus.CREATING:
                # Pod exists but has no runtime yet — check if it's been stuck pulling.
                age_sec: float | None = None
                if existing.created_at is not None:
                    age_sec = (datetime.now(timezone.utc) - existing.created_at).total_seconds()

                if age_sec is None or age_sec >= pull_stuck_timeout_sec:
                    log.warning(
                        "create_pod: existing pod %s (name=%s) has been CREATING for %s — "
                        "terminating to create fresh pod",
                        existing.id,
                        idempotency_key,
                        f"{int(age_sec)}s" if age_sec is not None else "unknown duration",
                    )
                    try:
                        await self.terminate_pod(existing.id)
                    except Exception:
                        log.exception(
                            "create_pod: failed to terminate stuck pod %s", existing.id
                        )
                    raise ImagePullStuckError(
                        f"Terminated stuck-creating pod {existing.id}; retry to create fresh pod"
                    )
                # Young CREATING pod — return it and let wait_for_ready handle it.
                return existing

        ports_str = ",".join(f"{p}/http" for p in spec.ports)
        env_dict = spec.env or {}

        def _create() -> object:
            kwargs: dict[str, object] = dict(
                name=idempotency_key,
                image_name=spec.image,
                gpu_type_id=spec.gpu_type,
                cloud_type=spec.cloud_type,
                container_disk_in_gb=spec.disk_size_gb,
                ports=ports_str,
                env=env_dict,
            )
            if spec.volume_gb:
                kwargs["volume_in_gb"] = spec.volume_gb
            if spec.min_download > 0:
                kwargs["min_download"] = spec.min_download
            if spec.min_upload > 0:
                kwargs["min_upload"] = spec.min_upload
            return runpod.create_pod(**kwargs)

        try:
            raw = _as_raw_pod(await asyncio.to_thread(_create))
            if raw is None or not raw.get("id"):
                raise NoInstancesAvailableError(
                    f"No instances available for GPU type: {spec.gpu_type}"
                )
            pod_id = str(raw["id"])
            # create_pod returns sparse data; fetch full pod info
            full_pod = await self.get_pod(pod_id, service_port)
            if full_pod:
                return full_pod
            # Fallback to parsing sparse response if get_pod fails
            return self._parse_pod(raw, service_port)
        except NoInstancesAvailableError:
            raise
        except Exception as exc:
            err_msg = str(exc).lower()
            if "no longer any instances available" in err_msg or "no instances available" in err_msg:
                raise NoInstancesAvailableError(
                    f"No instances available for GPU type: {spec.gpu_type}"
                ) from exc
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
        self,
        pod_id: str,
        timeout_sec: int = 1200,
        service_port: int | None = None,
        pull_stuck_timeout_sec: int = 480,
    ) -> GpuPod:
        """Wait for pod to be ready (health check passes).

        Args:
            pod_id: The pod ID to wait for.
            timeout_sec: Maximum time to wait for the pod to become ready.
            service_port: The service port to use for endpoint URL construction.
                If not specified, defaults to 8005 (TTS server). Use 8006 for image server.
            pull_stuck_timeout_sec: If the pod has no runtime (image still pulling)
                for longer than this many seconds, terminate it and raise
                ImagePullStuckError so the caller can create a fresh pod.
        """
        from .base import ImagePullStuckError

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        last_status: str = ""
        elapsed_log_interval = 30  # log status every 30 s
        last_log_time = loop.time()
        pull_stuck_since: float | None = None  # wall time when we first saw runtime=None

        while loop.time() < deadline:
            pod = await self.get_pod(pod_id, service_port)
            current_status = pod.status.value if pod else "unknown"

            now = loop.time()
            if current_status != last_status or (now - last_log_time) >= elapsed_log_interval:
                elapsed = int(now - (deadline - timeout_sec))
                log.info(
                    "wait_for_ready pod=%s status=%s elapsed=%ds",
                    pod_id, current_status, elapsed,
                )
                last_status = current_status
                last_log_time = now

            if pod and pod.endpoint_url:
                # Reset pull-stuck timer — the runtime appeared.
                pull_stuck_since = None
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        r = await client.get(f"{pod.endpoint_url}/health")
                        if r.status_code == 200:
                            pod.status = GpuPodStatus.READY
                            log.info(
                                "wait_for_ready pod=%s READY endpoint=%s",
                                pod_id, pod.endpoint_url,
                            )
                            return pod
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
            else:
                # No endpoint yet — image may still be pulling.
                if pull_stuck_since is None:
                    pull_stuck_since = now
                elif now - pull_stuck_since >= pull_stuck_timeout_sec:
                    stuck_secs = int(now - pull_stuck_since)
                    log.warning(
                        "wait_for_ready pod=%s image pull stuck for %ds — terminating",
                        pod_id, stuck_secs,
                    )
                    try:
                        await self.terminate_pod(pod_id)
                    except Exception:
                        log.exception("wait_for_ready: failed to terminate stuck pod %s", pod_id)
                    raise ImagePullStuckError(
                        f"Pod {pod_id} image pull stuck for {stuck_secs}s; pod terminated"
                    )

            await asyncio.sleep(5)

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

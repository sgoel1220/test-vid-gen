"""Vast.ai GPU provider implementation using the official vastai SDK."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.models.enums import GpuPodStatus, GpuProvider as GpuProviderName

from .base import GpuPod, GpuPodSpec, ImagePullStuckError, NoInstancesAvailableError

log = logging.getLogger(__name__)


def _normalize_gpu_name(runpod_name: str) -> str:
    """Convert RunPod-style GPU name to Vast.ai query format.

    Examples:
        "NVIDIA RTX A4000" → "RTX_A4000"
        "NVIDIA RTX 3080 Ti" → "RTX_3080_Ti"
        "NVIDIA A100 SXM4 80GB" → "A100_SXM4_80GB"
    """
    name = runpod_name.strip()
    if name.upper().startswith("NVIDIA "):
        name = name[7:]
    return name.replace(" ", "_")


def _parse_ports(ports_raw: object, container_port: int) -> int | None:
    """Extract the host port mapped to a given container port.

    Vast.ai returns ports as a JSON string or dict in Docker-style format:
    {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "34567"}]}
    or {"8080/tcp": "34567"}
    """
    if ports_raw is None:
        return None

    if isinstance(ports_raw, str):
        try:
            ports_raw = json.loads(ports_raw)
        except (json.JSONDecodeError, ValueError):
            return None

    if not isinstance(ports_raw, dict):
        return None

    for proto in (f"{container_port}/tcp", f"{container_port}/udp", str(container_port)):
        mapping = ports_raw.get(proto)
        if mapping is None:
            continue
        if isinstance(mapping, list) and mapping:
            entry = mapping[0]
            if isinstance(entry, dict):
                host_port = entry.get("HostPort")
                if host_port is not None:
                    try:
                        return int(str(host_port))
                    except ValueError:
                        pass
        elif isinstance(mapping, (str, int)):
            try:
                return int(str(mapping))
            except ValueError:
                pass

    return None


def _build_endpoint(instance: dict[str, Any], service_port: int | None) -> str | None:
    """Construct the HTTP endpoint URL from a Vast.ai instance dict."""
    public_ip = instance.get("public_ipaddr")
    if not isinstance(public_ip, str) or not public_ip:
        return None

    if service_port is None:
        return None

    ports_raw = instance.get("ports")
    host_port = _parse_ports(ports_raw, service_port)
    if host_port is None:
        return None

    return f"http://{public_ip}:{host_port}"


def _parse_status(instance: dict[str, Any]) -> GpuPodStatus:
    """Map Vast.ai actual_status to GpuPodStatus.

    Vast.ai documented states:
    - null / None: still provisioning (not yet assigned)
    - "loading": pulling image / booting
    - "running": container is up
    - "exited": container stopped
    - "rebooting", "starting", "frozen": transient / non-stoppable
    - "offline", "unknown": connectivity lost
    - "destroyed", "deleted": no longer billable
    """
    raw = instance.get("actual_status")
    if raw is None:
        # Instance is provisioning — not yet assigned a status
        return GpuPodStatus.CREATING

    actual_status = str(raw).lower()
    if actual_status == "running":
        return GpuPodStatus.RUNNING
    if actual_status in ("loading", "rebooting", "starting", "booting", "frozen"):
        return GpuPodStatus.CREATING
    if actual_status in ("exited", "stopped", "paused"):
        return GpuPodStatus.STOPPED
    if actual_status in ("destroyed", "deleted", "removed"):
        return GpuPodStatus.TERMINATED
    # offline, unknown, or anything else → STOPPED (not a provisioning state)
    return GpuPodStatus.STOPPED


def _as_instance(raw: object) -> dict[str, Any]:
    """Validate that a Vast.ai API response is a dict."""
    if not isinstance(raw, dict):
        raise TypeError(f"Expected Vast.ai instance payload to be a dict, got {type(raw).__name__}")
    return raw


def _as_instance_list(raw: object) -> list[dict[str, Any]]:
    """Validate that a Vast.ai API response is a list of dicts."""
    if not isinstance(raw, list):
        raise TypeError(f"Expected Vast.ai instance list to be a list, got {type(raw).__name__}")
    result: list[dict[str, Any]] = []
    for item in raw:
        result.append(_as_instance(item))
    return result


class VastAIProvider:
    """GPU provider backed by the Vast.ai SDK."""

    def __init__(
        self,
        api_key: str,
        min_reliability: float = 0.99,
        max_dph: float = 2.0,
        geo: str = "",
        cuda_min: float = 12.0,
    ) -> None:
        from vastai import VastAI

        self._client = VastAI(api_key=api_key)
        self._min_reliability = min_reliability
        self._max_dph = max_dph
        self._geo = geo
        self._cuda_min = cuda_min

    def _parse_pod(
        self, instance: dict[str, Any], service_port: int | None = None
    ) -> GpuPod:
        """Parse a Vast.ai instance dict into a GpuPod."""
        pod_id = str(instance["id"])
        status = _parse_status(instance)
        endpoint_url = _build_endpoint(instance, service_port)

        gpu_name = instance.get("gpu_name") or instance.get("gpu_displayname")
        gpu_type = str(gpu_name) if gpu_name else None

        dph = instance.get("dph_total") or instance.get("dph_base")
        cost_cents = int(float(dph) * 100) if dph else None

        created_at: datetime | None = None
        raw_ts = instance.get("start_date")
        if isinstance(raw_ts, (int, float)):
            try:
                created_at = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                pass

        return GpuPod(
            id=pod_id,
            provider=GpuProviderName.VASTAI,
            status=status,
            endpoint_url=endpoint_url,
            gpu_type=gpu_type,
            cost_per_hour_cents=cost_cents,
            created_at=created_at,
        )

    def _build_search_query(self, spec: GpuPodSpec) -> str:
        """Build a Vast.ai offer search query from a GpuPodSpec."""
        gpu_name = _normalize_gpu_name(spec.gpu_type)
        parts: list[str] = [
            f"gpu_name={gpu_name}",
            f"num_gpus={spec.gpu_count}",
            f"disk_space>={spec.disk_size_gb}",
            "direct_port_count>=1",
            f"reliability>={self._min_reliability}",
            f"dph_total<={self._max_dph}",
            f"cuda_max_good>={self._cuda_min}",
        ]
        if spec.min_download > 0:
            parts.append(f"inet_down>={spec.min_download}")
        if spec.min_upload > 0:
            parts.append(f"inet_up>={spec.min_upload}")
        if spec.cloud_type == "SECURE":
            parts.append("verified=true")
        if self._geo:
            parts.append(f"geolocation in [{self._geo}]")
        return " ".join(parts)

    async def _find_pod_by_label(
        self, label: str, service_port: int | None = None
    ) -> GpuPod | None:
        """Find an active instance by its label.

        Propagates API failures (fail-closed) so callers don't accidentally
        create a duplicate billable instance when the existence check fails.
        """

        def _list() -> object:
            return self._client.show_instances()

        # Let exceptions propagate — callers must not proceed to create a new
        # instance when the idempotency check cannot be completed.
        raw_list = _as_instance_list(await asyncio.to_thread(_list))

        for instance in raw_list:
            if instance.get("label") == label:
                pod = self._parse_pod(instance, service_port)
                if pod.status != GpuPodStatus.TERMINATED:
                    return pod
        return None

    async def create_pod(self, spec: GpuPodSpec, idempotency_key: str) -> GpuPod:
        """Create a new Vast.ai instance, or return an existing one with the same label."""
        pull_stuck_timeout_sec = 480
        service_port = spec.ports[0] if spec.ports else 8005

        existing = await self._find_pod_by_label(idempotency_key, service_port)
        if existing:
            if existing.status in (GpuPodStatus.RUNNING, GpuPodStatus.READY):
                return existing
            if existing.status == GpuPodStatus.STOPPED:
                log.info("Resuming stopped vastai instance %s (label=%s)", existing.id, idempotency_key)
                return await self.resume_pod(existing.id, spec.gpu_count, service_port)
            if existing.status == GpuPodStatus.CREATING:
                age_sec: float | None = None
                if existing.created_at is not None:
                    age_sec = (datetime.now(timezone.utc) - existing.created_at).total_seconds()
                if age_sec is None or age_sec >= pull_stuck_timeout_sec:
                    log.warning(
                        "create_pod: vastai instance %s (label=%s) stuck CREATING for %s — terminating",
                        existing.id,
                        idempotency_key,
                        f"{int(age_sec)}s" if age_sec is not None else "unknown duration",
                    )
                    try:
                        await self.terminate_pod(existing.id)
                    except Exception:
                        log.exception("create_pod: failed to terminate stuck vastai instance %s", existing.id)
                    raise ImagePullStuckError(
                        f"Terminated stuck-creating vastai instance {existing.id}; retry to create fresh instance"
                    )
                return existing

        query = self._build_search_query(spec)
        log.info("vastai search_offers query=%r", query)

        def _search() -> object:
            return self._client.search_offers(query=query, order="dph_total", limit="3")

        try:
            offers_raw = await asyncio.to_thread(_search)
        except Exception as exc:
            raise NoInstancesAvailableError(
                f"vastai search_offers failed: {exc}"
            ) from exc

        if not isinstance(offers_raw, list) or not offers_raw:
            raise NoInstancesAvailableError(
                f"No Vast.ai instances available matching: {query}"
            )

        offer = _as_instance(offers_raw[0])
        offer_id = int(offer["id"])

        # Build Docker-style env string: "-e KEY=VAL -e KEY2=VAL2"
        # Vast.ai create_instance passes env through to the container runtime.
        env_parts: list[str] = [f"-e {k}={v}" for k, v in spec.env.items()]
        # Publish the service port(s) so port mapping appears in the instance
        # response. Without explicit -p flags the container port is not mapped
        # to a host port, which prevents _build_endpoint from ever producing a
        # reachable URL.
        for p in spec.ports:
            env_parts.append(f"-p {p}:{p}")
        env_str = " ".join(env_parts) if env_parts else ""

        def _create() -> object:
            kwargs: dict[str, object] = dict(
                id=offer_id,
                image=spec.image,
                disk=float(spec.disk_size_gb),
                label=idempotency_key,
                direct=True,
            )
            if env_str:
                kwargs["env"] = env_str
            return self._client.create_instance(**kwargs)

        try:
            result = _as_instance(await asyncio.to_thread(_create))
        except Exception as exc:
            err_msg = str(exc).lower()
            if "no instances" in err_msg or "unavailable" in err_msg:
                raise NoInstancesAvailableError(
                    f"No Vast.ai instances available for GPU type: {spec.gpu_type}"
                ) from exc
            # Attempt idempotency recovery: a concurrent call may have succeeded.
            try:
                recovered = await self._find_pod_by_label(idempotency_key, service_port)
            except Exception:
                log.exception(
                    "create_pod: label lookup failed after create error; re-raising original error"
                )
                raise exc
            if recovered and recovered.status not in (GpuPodStatus.TERMINATED, GpuPodStatus.STOPPED):
                return recovered
            raise

        new_contract = result.get("new_contract")
        if new_contract is None:
            raise NoInstancesAvailableError(
                f"Vast.ai create_instance returned no contract for GPU type: {spec.gpu_type}"
            )
        new_id = str(new_contract)

        full_pod = await self.get_pod(new_id, service_port)
        if full_pod:
            return full_pod

        return GpuPod(
            id=new_id,
            provider=GpuProviderName.VASTAI,
            status=GpuPodStatus.CREATING,
            endpoint_url=None,
            gpu_type=spec.gpu_type,
            cost_per_hour_cents=int(float(offer.get("dph_total", 0)) * 100) or None,
            created_at=datetime.now(timezone.utc),
        )

    async def get_pod(self, pod_id: str, service_port: int | None = None) -> GpuPod | None:
        """Get Vast.ai instance status by ID."""

        def _get() -> object:
            return self._client.show_instance(id=int(pod_id))

        try:
            raw = await asyncio.to_thread(_get)
            if not raw:
                return None
            instance = _as_instance(raw)
            return self._parse_pod(instance, service_port)
        except Exception:
            log.exception("vastai show_instance failed pod_id=%s", pod_id)
            return None

    async def resume_pod(
        self, pod_id: str, gpu_count: int = 1, service_port: int | None = None
    ) -> GpuPod:
        """Resume a stopped Vast.ai instance."""

        def _start() -> object:
            return self._client.start_instance(id=int(pod_id))

        await asyncio.to_thread(_start)
        full_pod = await self.get_pod(pod_id, service_port)
        if full_pod:
            return full_pod
        raise RuntimeError(f"Vast.ai instance {pod_id} not found after start")

    async def terminate_pod(self, pod_id: str) -> bool:
        """Destroy a Vast.ai instance."""

        def _destroy() -> object:
            return self._client.destroy_instance(id=int(pod_id))

        try:
            await asyncio.to_thread(_destroy)
            return True
        except Exception:
            log.exception("vastai destroy_instance failed pod_id=%s", pod_id)
            return False

    async def wait_for_ready(
        self,
        pod_id: str,
        timeout_sec: int = 1200,
        service_port: int | None = None,
        pull_stuck_timeout_sec: int = 480,
    ) -> GpuPod:
        """Wait for a Vast.ai instance to pass the HTTP health check."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        last_status: str = ""
        elapsed_log_interval = 30
        last_log_time = loop.time()
        pull_stuck_since: float | None = None

        while loop.time() < deadline:
            pod = await self.get_pod(pod_id, service_port)
            current_status = pod.status.value if pod else "unknown"

            now = loop.time()
            if current_status != last_status or (now - last_log_time) >= elapsed_log_interval:
                elapsed = int(now - (deadline - timeout_sec))
                log.info(
                    "wait_for_ready vastai pod=%s status=%s elapsed=%ds",
                    pod_id, current_status, elapsed,
                )
                last_status = current_status
                last_log_time = now

            if pod and pod.endpoint_url:
                pull_stuck_since = None
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        r = await client.get(f"{pod.endpoint_url}/health")
                        if r.status_code == 200:
                            pod.status = GpuPodStatus.READY
                            log.info(
                                "wait_for_ready vastai pod=%s READY endpoint=%s",
                                pod_id, pod.endpoint_url,
                            )
                            return pod
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
            else:
                if pull_stuck_since is None:
                    pull_stuck_since = now
                elif now - pull_stuck_since >= pull_stuck_timeout_sec:
                    stuck_secs = int(now - pull_stuck_since)
                    log.warning(
                        "wait_for_ready vastai pod=%s image pull stuck for %ds — terminating",
                        pod_id, stuck_secs,
                    )
                    try:
                        await self.terminate_pod(pod_id)
                    except Exception:
                        log.exception("wait_for_ready: failed to terminate stuck vastai instance %s", pod_id)
                    raise ImagePullStuckError(
                        f"Vast.ai instance {pod_id} image pull stuck for {stuck_secs}s; instance terminated"
                    )

            await asyncio.sleep(5)

        raise TimeoutError(f"Vast.ai instance {pod_id} did not become ready within {timeout_sec}s")

    async def list_active_pods(self) -> list[GpuPod]:
        """List all non-destroyed Vast.ai instances."""

        def _list() -> object:
            return self._client.show_instances()

        raw_list = _as_instance_list(await asyncio.to_thread(_list))
        result: list[GpuPod] = []
        for instance in raw_list:
            actual = str(instance.get("actual_status", "")).lower()
            # Skip destroyed/terminated instances
            if actual in ("destroyed", "deleted"):
                continue
            pod = self._parse_pod(instance)
            if pod.status != GpuPodStatus.TERMINATED:
                result.append(pod)
        return result

    async def aclose(self) -> None:
        """No cleanup needed for SDK-based provider."""

"""Tests for VastAIProvider and the vastai integration path.

Covers:
- Helper functions (_normalize_gpu_name, _parse_ports, _build_endpoint, _parse_status)
- VastAIProvider._parse_pod
- VastAIProvider.create_pod (happy path, idempotency, stuck-creating, no offers)
- VastAIProvider.resume_pod, terminate_pod, list_active_pods
- Fallback GPU type logic via create_recorded_pod
- Full workflow via gpu_pod (create → wait → yield → terminate)
- workflow_gpu_pod provider selection from settings

All VastAI SDK calls are mocked; no network access required.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gpu.base import GpuPod, GpuPodSpec, NoInstancesAvailableError, ImagePullStuckError
from app.gpu.vastai import (
    VastAIProvider,
    _normalize_gpu_name,
    _parse_ports,
    _build_endpoint,
    _parse_status,
)
from app.models.enums import GpuPodStatus, GpuProvider as GpuProviderEnum


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_provider(
    *,
    api_key: str = "test-key",
    min_reliability: float = 0.99,
    max_dph: float = 2.0,
    geo: str = "",
    cuda_min: float = 12.0,
) -> VastAIProvider:
    """Instantiate VastAIProvider with a mocked VastAI SDK client.

    VastAI is imported lazily inside __init__, so we patch the module-level
    name in the vastai package, not app.gpu.vastai.
    """
    mock_sdk_instance = MagicMock()
    mock_sdk_class = MagicMock(return_value=mock_sdk_instance)

    # The import happens as `from vastai import VastAI` inside __init__,
    # so we patch the name in the vastai top-level module.
    import sys
    import types

    fake_vastai_mod = types.ModuleType("vastai")
    fake_vastai_mod.VastAI = mock_sdk_class  # type: ignore[attr-defined]

    orig = sys.modules.get("vastai")
    sys.modules["vastai"] = fake_vastai_mod
    try:
        provider = VastAIProvider(
            api_key=api_key,
            min_reliability=min_reliability,
            max_dph=max_dph,
            geo=geo,
            cuda_min=cuda_min,
        )
    finally:
        if orig is None:
            sys.modules.pop("vastai", None)
        else:
            sys.modules["vastai"] = orig

    # Replace the client with a fresh mock so each test can configure it
    provider._client = MagicMock()
    return provider


def _instance(
    *,
    id: int = 12345,
    actual_status: str | None = "running",
    public_ipaddr: str = "1.2.3.4",
    ports: object = None,
    label: str = "tts-run1",
    gpu_name: str = "RTX_A4000",
    dph_total: float = 0.55,
    start_date: float = 1700000000.0,
) -> dict[str, Any]:
    """Build a minimal Vast.ai instance dict."""
    return {
        "id": id,
        "actual_status": actual_status,
        "public_ipaddr": public_ipaddr,
        "ports": ports,
        "label": label,
        "gpu_name": gpu_name,
        "dph_total": dph_total,
        "start_date": start_date,
    }


def _make_session_maker() -> MagicMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    maker = MagicMock()
    maker.return_value = session
    return maker


def _make_spec(
    *,
    gpu_type: str = "NVIDIA RTX A4000",
    ports: list[int] | None = None,
) -> GpuPodSpec:
    return GpuPodSpec(
        gpu_type=gpu_type,
        image="ghcr.io/test/tts:latest",
        disk_size_gb=20,
        volume_gb=0,
        ports=ports or [8005],
        env={"MODEL": "tts-v1"},
    )


# ---------------------------------------------------------------------------
# _normalize_gpu_name
# ---------------------------------------------------------------------------


class TestNormalizeGpuName:
    def test_strips_nvidia_prefix(self) -> None:
        assert _normalize_gpu_name("NVIDIA RTX A4000") == "RTX_A4000"

    def test_replaces_spaces_with_underscores(self) -> None:
        assert _normalize_gpu_name("RTX 3080 Ti") == "RTX_3080_Ti"

    def test_handles_a100_variant(self) -> None:
        assert _normalize_gpu_name("NVIDIA A100 SXM4 80GB") == "A100_SXM4_80GB"

    def test_no_prefix_unchanged_underscores(self) -> None:
        assert _normalize_gpu_name("RTX_4090") == "RTX_4090"

    def test_lowercase_nvidia_is_also_stripped(self) -> None:
        # _normalize_gpu_name checks .upper().startswith("NVIDIA ") — case-insensitive
        result = _normalize_gpu_name("nvidia RTX A4000")
        assert result == "RTX_A4000"


# ---------------------------------------------------------------------------
# _parse_ports
# ---------------------------------------------------------------------------


class TestParsePorts:
    def test_docker_list_format(self) -> None:
        ports_raw = {"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "34567"}]}
        assert _parse_ports(ports_raw, 8005) == 34567

    def test_docker_string_value_format(self) -> None:
        ports_raw = {"8005/tcp": "34567"}
        assert _parse_ports(ports_raw, 8005) == 34567

    def test_udp_key_fallback(self) -> None:
        ports_raw = {"8005/udp": [{"HostIp": "0.0.0.0", "HostPort": "34568"}]}
        assert _parse_ports(ports_raw, 8005) == 34568

    def test_plain_port_key(self) -> None:
        ports_raw = {"8005": "9999"}
        assert _parse_ports(ports_raw, 8005) == 9999

    def test_json_string_input(self) -> None:
        raw = json.dumps({"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "11111"}]})
        assert _parse_ports(raw, 8005) == 11111

    def test_returns_none_on_missing_port(self) -> None:
        ports_raw = {"9000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "34567"}]}
        assert _parse_ports(ports_raw, 8005) is None

    def test_returns_none_on_none_input(self) -> None:
        assert _parse_ports(None, 8005) is None

    def test_returns_none_on_bad_json(self) -> None:
        assert _parse_ports("not-json", 8005) is None

    def test_returns_none_on_non_dict_list(self) -> None:
        assert _parse_ports([1, 2, 3], 8005) is None


# ---------------------------------------------------------------------------
# _build_endpoint
# ---------------------------------------------------------------------------


class TestBuildEndpoint:
    def _ports(self) -> dict[str, object]:
        return {"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "20000"}]}

    def test_constructs_url(self) -> None:
        inst = _instance(ports=self._ports())
        url = _build_endpoint(inst, 8005)
        assert url == "http://1.2.3.4:20000"

    def test_returns_none_when_no_public_ip(self) -> None:
        inst = _instance(public_ipaddr="", ports=self._ports())
        assert _build_endpoint(inst, 8005) is None

    def test_returns_none_when_service_port_is_none(self) -> None:
        inst = _instance(ports=self._ports())
        assert _build_endpoint(inst, None) is None

    def test_returns_none_when_port_not_mapped(self) -> None:
        inst = _instance(ports={"9999/tcp": "8888"})
        assert _build_endpoint(inst, 8005) is None


# ---------------------------------------------------------------------------
# _parse_status
# ---------------------------------------------------------------------------


class TestParseStatus:
    def test_running(self) -> None:
        assert _parse_status({"actual_status": "running"}) == GpuPodStatus.RUNNING

    def test_loading_is_creating(self) -> None:
        assert _parse_status({"actual_status": "loading"}) == GpuPodStatus.CREATING

    def test_none_status_is_creating(self) -> None:
        assert _parse_status({"actual_status": None}) == GpuPodStatus.CREATING

    def test_missing_key_is_creating(self) -> None:
        assert _parse_status({}) == GpuPodStatus.CREATING

    def test_exited_is_stopped(self) -> None:
        assert _parse_status({"actual_status": "exited"}) == GpuPodStatus.STOPPED

    def test_destroyed_is_terminated(self) -> None:
        assert _parse_status({"actual_status": "destroyed"}) == GpuPodStatus.TERMINATED

    def test_deleted_is_terminated(self) -> None:
        assert _parse_status({"actual_status": "deleted"}) == GpuPodStatus.TERMINATED

    def test_offline_is_stopped(self) -> None:
        assert _parse_status({"actual_status": "offline"}) == GpuPodStatus.STOPPED

    def test_unknown_is_stopped(self) -> None:
        assert _parse_status({"actual_status": "unknown"}) == GpuPodStatus.STOPPED

    def test_rebooting_is_creating(self) -> None:
        assert _parse_status({"actual_status": "rebooting"}) == GpuPodStatus.CREATING


# ---------------------------------------------------------------------------
# VastAIProvider._parse_pod
# ---------------------------------------------------------------------------


class TestVastAIProviderParsePod:
    def _provider(self) -> VastAIProvider:
        return _make_provider()

    def test_running_instance_full_fields(self) -> None:
        ports = {"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "20000"}]}
        inst = _instance(ports=ports)
        pod = self._provider()._parse_pod(inst, service_port=8005)

        assert pod.id == "12345"
        assert pod.provider == GpuProviderEnum.VASTAI
        assert pod.status == GpuPodStatus.RUNNING
        assert pod.endpoint_url == "http://1.2.3.4:20000"
        assert pod.gpu_type == "RTX_A4000"
        assert pod.cost_per_hour_cents == 55  # 0.55 * 100
        assert pod.created_at is not None

    def test_no_ports_no_endpoint(self) -> None:
        pod = self._provider()._parse_pod(_instance(ports=None))
        assert pod.endpoint_url is None

    def test_loading_status(self) -> None:
        pod = self._provider()._parse_pod(_instance(actual_status="loading"))
        assert pod.status == GpuPodStatus.CREATING

    def test_destroyed_status(self) -> None:
        pod = self._provider()._parse_pod(_instance(actual_status="destroyed"))
        assert pod.status == GpuPodStatus.TERMINATED

    def test_start_date_parsed(self) -> None:
        pod = self._provider()._parse_pod(_instance(start_date=1700000000.0))
        assert pod.created_at is not None
        assert pod.created_at.tzinfo is not None

    def test_missing_start_date_yields_none(self) -> None:
        inst = _instance()
        del inst["start_date"]
        pod = self._provider()._parse_pod(inst)
        assert pod.created_at is None

    def test_dph_fallback_to_dph_base(self) -> None:
        inst = _instance(dph_total=0.0)
        inst["dph_base"] = 0.40
        inst["dph_total"] = None
        pod = self._provider()._parse_pod(inst)
        assert pod.cost_per_hour_cents == 40

    def test_gpu_displayname_fallback(self) -> None:
        inst = _instance()
        inst["gpu_name"] = None
        inst["gpu_displayname"] = "RTX_4090"
        pod = self._provider()._parse_pod(inst)
        assert pod.gpu_type == "RTX_4090"


# ---------------------------------------------------------------------------
# VastAIProvider.create_pod
# ---------------------------------------------------------------------------


class TestCreatePod:
    @pytest.mark.asyncio
    async def test_happy_path_creates_instance(self) -> None:
        provider = _make_provider()
        ports = {"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "20000"}]}

        # No existing instance with this label
        provider._client.show_instances.return_value = []
        # search_offers returns one offer
        provider._client.search_offers.return_value = [{"id": 99, "dph_total": 0.50}]
        # create_instance returns a new_contract ID
        provider._client.create_instance.return_value = {"new_contract": 12345}
        # show_instance returns running instance
        provider._client.show_instance.return_value = _instance(
            id=12345, ports=ports, actual_status="running"
        )

        pod = await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

        assert pod.id == "12345"
        assert pod.status == GpuPodStatus.RUNNING
        assert pod.provider == GpuProviderEnum.VASTAI
        provider._client.create_instance.assert_called_once()
        # Verify label was set
        call_kwargs = provider._client.create_instance.call_args.kwargs
        assert call_kwargs["label"] == "tts-run1"

    @pytest.mark.asyncio
    async def test_idempotent_returns_existing_running(self) -> None:
        """create_pod with an existing running instance returns it without creating a new one."""
        provider = _make_provider()
        ports = {"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "20000"}]}
        existing = _instance(id=99, label="tts-run1", ports=ports, actual_status="running")
        provider._client.show_instances.return_value = [existing]

        pod = await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

        assert pod.id == "99"
        # Should NOT call create_instance
        provider._client.create_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotent_resumes_stopped_instance(self) -> None:
        """A stopped instance with matching label is resumed instead of creating a new one."""
        provider = _make_provider()
        ports = {"8005/tcp": [{"HostIp": "0.0.0.0", "HostPort": "20000"}]}
        stopped = _instance(id=77, label="tts-run1", actual_status="exited")
        provider._client.show_instances.return_value = [stopped]
        provider._client.start_instance.return_value = {"success": True}
        provider._client.show_instance.return_value = _instance(
            id=77, ports=ports, actual_status="running"
        )

        pod = await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

        assert pod.id == "77"
        provider._client.start_instance.assert_called_once()
        provider._client.create_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_offers_raises_no_instances_available(self) -> None:
        provider = _make_provider()
        provider._client.show_instances.return_value = []
        provider._client.search_offers.return_value = []

        with pytest.raises(NoInstancesAvailableError, match="No Vast.ai instances"):
            await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

    @pytest.mark.asyncio
    async def test_search_exception_raises_no_instances_available(self) -> None:
        provider = _make_provider()
        provider._client.show_instances.return_value = []
        provider._client.search_offers.side_effect = RuntimeError("API error")

        with pytest.raises(NoInstancesAvailableError, match="search_offers failed"):
            await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

    @pytest.mark.asyncio
    async def test_stuck_creating_instance_is_terminated_and_raises(self) -> None:
        """A CREATING instance older than pull_stuck_timeout_sec is terminated and raises."""
        provider = _make_provider()
        # Instance has been creating since way in the past (epoch 0)
        stuck = _instance(id=55, label="tts-run1", actual_status="loading", start_date=0.0)
        provider._client.show_instances.return_value = [stuck]
        provider._client.destroy_instance.return_value = {"success": True}

        with pytest.raises(ImagePullStuckError):
            await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

        provider._client.destroy_instance.assert_called_once_with(id=55)

    @pytest.mark.asyncio
    async def test_creating_instance_missing_start_date_is_not_destroyed(self) -> None:
        """A CREATING instance with unknown age is returned as-is; wait_for_ready handles stuck detection."""
        provider = _make_provider()
        # Instance is loading but has no start_date (some Vast.ai responses omit it)
        loading = _instance(id=77, label="tts-run1", actual_status="loading")
        del loading["start_date"]
        provider._client.show_instances.return_value = [loading]

        pod = await provider.create_pod(_make_spec(), idempotency_key="tts-run1")

        assert pod.id == "77"
        assert pod.status == GpuPodStatus.CREATING
        # Must NOT have destroyed the instance
        provider._client.destroy_instance.assert_not_called()
        provider._client.create_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_env_vars_passed_to_create_instance(self) -> None:
        provider = _make_provider()
        provider._client.show_instances.return_value = []
        provider._client.search_offers.return_value = [{"id": 99, "dph_total": 0.55}]
        provider._client.create_instance.return_value = {"new_contract": 12345}
        provider._client.show_instance.return_value = _instance(id=12345)

        spec = GpuPodSpec(
            gpu_type="NVIDIA RTX A4000",
            image="ghcr.io/test/tts:latest",
            disk_size_gb=20,
            volume_gb=0,
            ports=[8005],
            env={"MODEL": "tts-v1", "CUDA_VISIBLE_DEVICES": "0"},
        )
        await provider.create_pod(spec, idempotency_key="tts-run1")

        call_kwargs = provider._client.create_instance.call_args.kwargs
        env_str = call_kwargs.get("env", "")
        assert "-e MODEL=tts-v1" in env_str
        assert "-e CUDA_VISIBLE_DEVICES=0" in env_str
        assert "-p 8005:8005" in env_str


# ---------------------------------------------------------------------------
# VastAIProvider.terminate_pod
# ---------------------------------------------------------------------------


class TestTerminatePod:
    @pytest.mark.asyncio
    async def test_happy_path_returns_true(self) -> None:
        provider = _make_provider()
        provider._client.destroy_instance.return_value = {"success": True}

        result = await provider.terminate_pod("12345")

        assert result is True
        provider._client.destroy_instance.assert_called_once_with(id=12345)

    @pytest.mark.asyncio
    async def test_exception_returns_false(self) -> None:
        provider = _make_provider()
        provider._client.destroy_instance.side_effect = RuntimeError("API down")

        result = await provider.terminate_pod("12345")

        assert result is False


# ---------------------------------------------------------------------------
# VastAIProvider.list_active_pods
# ---------------------------------------------------------------------------


class TestListActivePods:
    @pytest.mark.asyncio
    async def test_excludes_destroyed_instances(self) -> None:
        provider = _make_provider()
        provider._client.show_instances.return_value = [
            _instance(id=1, actual_status="running"),
            _instance(id=2, actual_status="destroyed"),
            _instance(id=3, actual_status="loading"),
        ]

        pods = await provider.list_active_pods()

        ids = {p.id for p in pods}
        assert "1" in ids
        assert "3" in ids
        assert "2" not in ids

    @pytest.mark.asyncio
    async def test_empty_list(self) -> None:
        provider = _make_provider()
        provider._client.show_instances.return_value = []

        pods = await provider.list_active_pods()

        assert pods == []


# ---------------------------------------------------------------------------
# _build_search_query — geo and cloud_type paths
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    def test_geo_included_when_set(self) -> None:
        provider = _make_provider(geo="US,CA")
        query = provider._build_search_query(_make_spec())
        assert "geolocation in [US,CA]" in query

    def test_geo_excluded_when_empty(self) -> None:
        provider = _make_provider(geo="")
        query = provider._build_search_query(_make_spec())
        assert "geolocation" not in query

    def test_secure_cloud_adds_verified(self) -> None:
        provider = _make_provider()
        spec = GpuPodSpec(
            gpu_type="NVIDIA RTX A4000",
            image="ghcr.io/test/tts:latest",
            disk_size_gb=20,
            volume_gb=0,
            ports=[8005],
            cloud_type="SECURE",
        )
        query = provider._build_search_query(spec)
        assert "verified=true" in query

    def test_reliability_and_dph_in_query(self) -> None:
        provider = _make_provider(min_reliability=0.98, max_dph=1.5)
        query = provider._build_search_query(_make_spec())
        assert "reliability>=0.98" in query
        assert "dph_total<=1.5" in query

    def test_gpu_name_normalized(self) -> None:
        provider = _make_provider()
        query = provider._build_search_query(_make_spec(gpu_type="NVIDIA RTX A4000"))
        assert "gpu_name=RTX_A4000" in query


# ---------------------------------------------------------------------------
# Fallback GPU type via create_recorded_pod
# ---------------------------------------------------------------------------


class TestFallbackGpuType:
    @pytest.mark.asyncio
    async def test_fallback_used_when_primary_unavailable(self) -> None:
        """create_recorded_pod falls back to next GPU type on NoInstancesAvailableError."""
        from app.gpu import lifecycle

        fallback_pod = GpuPod(
            id="pod-fallback",
            provider=GpuProviderEnum.VASTAI,
            status=GpuPodStatus.RUNNING,
            endpoint_url="http://1.2.3.4:20000",
            gpu_type="NVIDIA RTX 3080 Ti",
            cost_per_hour_cents=40,
            created_at=datetime.now(timezone.utc),
        )

        provider = MagicMock()
        provider.create_pod = AsyncMock(
            side_effect=[NoInstancesAvailableError("no A4000"), fallback_pod]
        )

        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCS:
            cost_svc = AsyncMock()
            MockCS.return_value = cost_svc

            pod = await lifecycle.create_recorded_pod(
                provider,
                session_maker,
                spec=_make_spec(),
                idempotency_key="tts-run1",
                workflow_id=None,
                label="tts",
                gpu_type_fallbacks=["NVIDIA RTX 3080 Ti"],
            )

        assert pod.id == "pod-fallback"
        assert provider.create_pod.await_count == 2
        cost_svc.record_pod.assert_awaited_once()


# ---------------------------------------------------------------------------
# Full TTS workflow via gpu_pod with VastAI provider
# ---------------------------------------------------------------------------


class TestGpuPodWorkflowVastAI:
    """Simulate the full create → wait → TTS work → terminate lifecycle with VastAI."""

    def _make_vastai_pod(
        self, pod_id: str = "12345", status: GpuPodStatus = GpuPodStatus.RUNNING
    ) -> GpuPod:
        return GpuPod(
            id=pod_id,
            provider=GpuProviderEnum.VASTAI,
            status=status,
            endpoint_url="http://1.2.3.4:20000",
            gpu_type="NVIDIA RTX A4000",
            cost_per_hour_cents=55,
            created_at=datetime.now(timezone.utc),
        )

    @pytest.mark.asyncio
    async def test_full_workflow_creates_and_terminates(self) -> None:
        from app.gpu import lifecycle

        created = self._make_vastai_pod()
        ready = self._make_vastai_pod(status=GpuPodStatus.READY)

        provider = MagicMock()
        provider.create_pod = AsyncMock(return_value=created)
        provider.wait_for_ready = AsyncMock(return_value=ready)
        provider.terminate_pod = AsyncMock(return_value=True)
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCS:
            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=5)
            MockCS.return_value = cost_svc

            async with lifecycle.gpu_pod(
                provider,
                session_maker,
                spec=_make_spec(),
                idempotency_key="tts-run1",
                workflow_id=None,
                label="tts",
                timeout_sec=600,
                service_port=8005,
            ) as (pod, url):
                assert pod.id == "12345"
                assert url == "http://1.2.3.4:20000"
                # Simulate TTS work succeeding
                work_result = "audio generated"

        assert work_result == "audio generated"
        provider.terminate_pod.assert_awaited_once_with("12345")
        cost_svc.finalize_cost.assert_awaited_once_with("12345", reason="normal")

    @pytest.mark.asyncio
    async def test_terminate_called_on_workflow_failure(self) -> None:
        """Instance is destroyed even when the TTS work body raises."""
        from app.gpu import lifecycle

        created = self._make_vastai_pod()
        ready = self._make_vastai_pod(status=GpuPodStatus.READY)

        provider = MagicMock()
        provider.create_pod = AsyncMock(return_value=created)
        provider.wait_for_ready = AsyncMock(return_value=ready)
        provider.terminate_pod = AsyncMock(return_value=True)
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCS:
            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=0)
            MockCS.return_value = cost_svc

            with pytest.raises(RuntimeError, match="TTS failed"):
                async with lifecycle.gpu_pod(
                    provider,
                    session_maker,
                    spec=_make_spec(),
                    idempotency_key="tts-run1",
                    workflow_id=None,
                    label="tts",
                    timeout_sec=600,
                ) as _:
                    raise RuntimeError("TTS failed")

        provider.terminate_pod.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_terminate_does_not_finalize_cost(self) -> None:
        """When terminate_pod returns False, finalize_cost must NOT be called (pod may still be running)."""
        from app.gpu import lifecycle

        created = self._make_vastai_pod()
        ready = self._make_vastai_pod(status=GpuPodStatus.READY)

        provider = MagicMock()
        provider.create_pod = AsyncMock(return_value=created)
        provider.wait_for_ready = AsyncMock(return_value=ready)
        provider.terminate_pod = AsyncMock(return_value=False)  # termination failed
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCS:
            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=0)
            MockCS.return_value = cost_svc

            # terminate_and_finalize should raise, but gpu_pod swallows it
            async with lifecycle.gpu_pod(
                provider,
                session_maker,
                spec=_make_spec(),
                idempotency_key="tts-run1",
                workflow_id=None,
                label="tts",
                timeout_sec=600,
            ) as _:
                pass

        # Cost must NOT have been finalized — the pod is still running
        cost_svc.finalize_cost.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cost_tracking_records_correct_values(self) -> None:
        from app.gpu import lifecycle

        created = self._make_vastai_pod()
        ready = self._make_vastai_pod(status=GpuPodStatus.READY)

        provider = MagicMock()
        provider.create_pod = AsyncMock(return_value=created)
        provider.wait_for_ready = AsyncMock(return_value=ready)
        provider.terminate_pod = AsyncMock(return_value=True)
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCS:
            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=12)
            MockCS.return_value = cost_svc

            async with lifecycle.gpu_pod(
                provider,
                session_maker,
                spec=_make_spec(),
                idempotency_key="tts-run1",
                workflow_id=None,
                label="tts",
                timeout_sec=600,
                service_port=8005,
            ) as _:
                pass

        record_call = cost_svc.record_pod.call_args
        assert record_call.kwargs["provider"] == GpuProviderEnum.VASTAI
        assert record_call.kwargs["cost_per_hour_cents"] == 55


# ---------------------------------------------------------------------------
# workflow_gpu_pod provider selection from settings
# ---------------------------------------------------------------------------


class TestWorkflowGpuPodProviderSelection:
    @pytest.mark.asyncio
    async def test_vastai_provider_selected_when_configured(self) -> None:
        """workflow_gpu_pod builds a VastAIProvider when gpu_provider='vastai'."""
        from app.gpu import lifecycle

        created = GpuPod(
            id="vast-1",
            provider=GpuProviderEnum.VASTAI,
            status=GpuPodStatus.RUNNING,
            endpoint_url="http://1.2.3.4:20000",
            gpu_type="RTX_A4000",
            cost_per_hour_cents=50,
            created_at=datetime.now(timezone.utc),
        )
        ready = created.model_copy(update={"status": GpuPodStatus.READY})

        mock_provider = MagicMock()
        mock_provider.create_pod = AsyncMock(return_value=created)
        mock_provider.wait_for_ready = AsyncMock(return_value=ready)
        mock_provider.terminate_pod = AsyncMock(return_value=True)

        session_maker = _make_session_maker()

        with (
            patch("app.gpu.get_provider_from_settings", return_value=mock_provider),
            patch("app.config.settings") as mock_settings,
            patch.object(lifecycle, "CostService") as MockCS,
        ):
            mock_settings.gpu_provider = "vastai"
            mock_settings.gpu_type_fallbacks = []
            mock_settings.pod_ready_timeout_sec = 600

            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=10)
            MockCS.return_value = cost_svc

            async with lifecycle.workflow_gpu_pod(
                session_maker,
                spec=_make_spec(),
                idempotency_key="tts-wf1",
                workflow_id=None,
                label="tts",
                service_port=8005,
            ) as (pod, url):
                pass

        assert pod.id == "vast-1"
        assert url == "http://1.2.3.4:20000"

    @pytest.mark.asyncio
    async def test_get_provider_from_settings_vastai(self) -> None:
        """get_provider_from_settings returns VastAIProvider when gpu_provider='vastai'."""
        import sys, types
        from app.gpu import get_provider_from_settings

        fake_vastai_mod = types.ModuleType("vastai")
        fake_vastai_mod.VastAI = MagicMock()  # type: ignore[attr-defined]
        orig = sys.modules.get("vastai")
        sys.modules["vastai"] = fake_vastai_mod
        try:
            # get_provider_from_settings does `from app.config import settings` lazily
            with patch("app.config.settings") as mock_settings:
                mock_settings.gpu_provider = "vastai"
                mock_settings.vastai_api_key = "test-key"
                mock_settings.vastai_min_reliability = 0.99
                mock_settings.vastai_max_dph = 2.0
                mock_settings.vastai_geo = ""
                mock_settings.vastai_cuda_min = 12.0

                provider = get_provider_from_settings("vastai")
        finally:
            if orig is None:
                sys.modules.pop("vastai", None)
            else:
                sys.modules["vastai"] = orig

        assert isinstance(provider, VastAIProvider)

    @pytest.mark.asyncio
    async def test_get_provider_from_settings_runpod_default(self) -> None:
        """get_provider_from_settings returns RunPodProvider when gpu_provider='runpod'."""
        from app.gpu import get_provider_from_settings
        from app.gpu.runpod import RunPodProvider

        with patch("app.config.settings") as mock_settings:
            mock_settings.gpu_provider = "runpod"
            mock_settings.runpod_api_key = "rp-key"

            provider = get_provider_from_settings("runpod")

        assert isinstance(provider, RunPodProvider)

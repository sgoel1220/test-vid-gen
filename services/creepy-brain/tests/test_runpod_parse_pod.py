"""Unit tests for RunPodProvider._parse_pod."""

from __future__ import annotations

import pytest

from app.models.enums import GpuPodStatus
from app.gpu.runpod import RunPodProvider


def _provider() -> RunPodProvider:
    """Return a provider instance without making any network calls."""
    return RunPodProvider(api_key="test-key")


def _base_raw(*, desired: str = "RUNNING", ports: list[dict[str, object]] | None = None) -> dict[str, object]:
    raw: dict[str, object] = {
        "id": "pod-123",
        "name": "test-pod",
        "desiredStatus": desired,
        "createdAt": "2024-01-15T12:00:00Z",
        "runtime": {"ports": ports or []},
        "machine": {"gpuDisplayName": "RTX 4090", "costPerGpu": 0.50},
    }
    return raw


class TestParsePodStatus:
    def test_running(self) -> None:
        pod = _provider()._parse_pod(_base_raw(desired="RUNNING"))
        assert pod.status == GpuPodStatus.RUNNING

    def test_exited(self) -> None:
        pod = _provider()._parse_pod(_base_raw(desired="EXITED"))
        assert pod.status == GpuPodStatus.TERMINATED

    def test_terminated(self) -> None:
        pod = _provider()._parse_pod(_base_raw(desired="TERMINATED"))
        assert pod.status == GpuPodStatus.TERMINATED

    def test_unknown_maps_to_creating(self) -> None:
        pod = _provider()._parse_pod(_base_raw(desired="PENDING"))
        assert pod.status == GpuPodStatus.CREATING


class TestParsePodEndpoint:
    def _ports(self) -> list[dict[str, object]]:
        return [
            # SSH port exposed publicly — should NOT be selected when service_port=8005
            {"ip": "1.2.3.4", "privatePort": 22, "publicPort": 10022, "isIpPublic": True, "type": "tcp"},
            # Service port
            {"ip": "1.2.3.4", "privatePort": 8005, "publicPort": 20000, "isIpPublic": True, "type": "http"},
            # Another port
            {"ip": "1.2.3.4", "privatePort": 9000, "publicPort": 20001, "isIpPublic": True, "type": "http"},
        ]

    def test_selects_correct_private_port(self) -> None:
        pod = _provider()._parse_pod(_base_raw(ports=self._ports()), service_port=8005)
        assert pod.endpoint_url == "http://1.2.3.4:20000"

    def test_falls_back_to_first_public_when_no_service_port(self) -> None:
        pod = _provider()._parse_pod(_base_raw(ports=self._ports()), service_port=None)
        # First public port in iteration order
        assert pod.endpoint_url == "http://1.2.3.4:10022"

    def test_no_public_port_returns_none_endpoint(self) -> None:
        private_only = [
            {"ip": "10.0.0.1", "privatePort": 8005, "publicPort": 8005, "isIpPublic": False, "type": "http"},
        ]
        pod = _provider()._parse_pod(_base_raw(ports=private_only), service_port=8005)
        assert pod.endpoint_url is None

    def test_no_matching_service_port_returns_none(self) -> None:
        ports = [
            {"ip": "1.2.3.4", "privatePort": 9000, "publicPort": 20001, "isIpPublic": True, "type": "http"},
        ]
        pod = _provider()._parse_pod(_base_raw(ports=ports), service_port=8005)
        assert pod.endpoint_url is None

    def test_empty_ports_returns_none_endpoint(self) -> None:
        pod = _provider()._parse_pod(_base_raw(ports=[]), service_port=8005)
        assert pod.endpoint_url is None


class TestParsePodCreatedAt:
    def test_parses_iso_timestamp(self) -> None:
        pod = _provider()._parse_pod(_base_raw())
        assert pod.created_at is not None
        assert pod.created_at.year == 2024

    def test_none_when_missing(self) -> None:
        raw = _base_raw()
        del raw["createdAt"]
        pod = _provider()._parse_pod(raw)
        assert pod.created_at is None

    def test_none_on_malformed_timestamp(self) -> None:
        raw = _base_raw()
        raw["createdAt"] = "not-a-date"
        pod = _provider()._parse_pod(raw)
        assert pod.created_at is None


class TestParsePodMachineErrors:
    def test_raises_on_non_dict_machine(self) -> None:
        raw = _base_raw()
        raw["machine"] = "invalid"
        with pytest.raises(TypeError, match="Expected 'machine' to be a dict"):
            _provider()._parse_pod(raw)

    def test_none_machine_is_ok(self) -> None:
        raw = _base_raw()
        raw["machine"] = None
        pod = _provider()._parse_pod(raw)
        assert pod.gpu_type is None
        assert pod.cost_per_hour_cents is None

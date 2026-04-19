"""Unit tests for app.gpu.lifecycle — create_recorded_pod, wait_for_recorded_ready,
terminate_and_finalize, and the gpu_pod async context manager.

All external dependencies (GpuProvider, CostService, SQLAlchemy) are mocked so these
tests never hit a network or database.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.gpu.base import GpuPod, GpuPodSpec, GpuProvider, NoInstancesAvailableError
from app.models.enums import GpuPodStatus, GpuProvider as GpuProviderEnum

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POD_ID = "pod-abc123"
_ENDPOINT = "http://1.2.3.4:8080"


def _make_pod(
    pod_id: str = _POD_ID,
    status: GpuPodStatus = GpuPodStatus.RUNNING,
    endpoint_url: str | None = _ENDPOINT,
) -> GpuPod:
    return GpuPod(
        id=pod_id,
        provider=GpuProviderEnum.RUNPOD,
        status=status,
        endpoint_url=endpoint_url,
        gpu_type="NVIDIA RTX 4090",
        cost_per_hour_cents=50,
        created_at=datetime(2024, 1, 1),
    )


def _make_spec() -> GpuPodSpec:
    return GpuPodSpec(
        gpu_type="NVIDIA RTX 4090",
        image="ghcr.io/test/tts:latest",
        disk_size_gb=20,
        volume_gb=0,
        ports=[8080],
    )


def _make_provider(
    *,
    create_returns: GpuPod | None = None,
    wait_returns: GpuPod | None = None,
    terminate_returns: bool = True,
) -> MagicMock:
    """Return a mock GpuProvider with sane async defaults."""
    provider = MagicMock(spec=GpuProvider)
    provider.create_pod = AsyncMock(return_value=create_returns or _make_pod())
    provider.wait_for_ready = AsyncMock(return_value=wait_returns or _make_pod())
    provider.terminate_pod = AsyncMock(return_value=terminate_returns)
    return provider


def _make_session_maker() -> MagicMock:
    """Return a mock async_sessionmaker whose sessions are no-ops."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    session_maker = MagicMock()
    session_maker.return_value = session
    return session_maker


# ---------------------------------------------------------------------------
# create_recorded_pod
# ---------------------------------------------------------------------------

class TestCreateRecordedPod:
    """Tests for lifecycle.create_recorded_pod."""

    @pytest.mark.asyncio
    async def test_happy_path_no_fallbacks(self) -> None:
        """Creates a pod and records cost; returns the pod."""
        from app.gpu import lifecycle

        provider = _make_provider()
        session_maker = _make_session_maker()
        spec = _make_spec()
        wf_id = uuid.uuid4()

        with patch.object(lifecycle, "CostService") as MockCostService:
            cost_svc = AsyncMock()
            MockCostService.return_value = cost_svc

            pod = await lifecycle.create_recorded_pod(
                provider,
                session_maker,
                spec=spec,
                idempotency_key="tts-abc",
                workflow_id=wf_id,
                label="tts",
            )

        assert pod.id == _POD_ID
        provider.create_pod.assert_awaited_once_with(spec=spec, idempotency_key="tts-abc")
        cost_svc.record_pod.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_used_on_no_instances(self) -> None:
        """First GPU type raises NoInstancesAvailableError; fallback succeeds."""
        from app.gpu import lifecycle

        fallback_pod = _make_pod(pod_id="pod-fallback")

        provider = _make_provider()
        provider.create_pod = AsyncMock(
            side_effect=[NoInstancesAvailableError("no RTX"), fallback_pod]
        )

        session_maker = _make_session_maker()
        spec = _make_spec()

        with patch.object(lifecycle, "CostService") as MockCostService:
            cost_svc = AsyncMock()
            MockCostService.return_value = cost_svc

            pod = await lifecycle.create_recorded_pod(
                provider,
                session_maker,
                spec=spec,
                idempotency_key="tts-abc",
                workflow_id=None,
                label="tts",
                gpu_type_fallbacks=["NVIDIA RTX 3090"],
            )

        assert pod.id == "pod-fallback"
        assert provider.create_pod.await_count == 2

    @pytest.mark.asyncio
    async def test_all_gpu_types_exhausted_raises(self) -> None:
        """All candidates raise NoInstancesAvailableError; exception is propagated."""
        from app.gpu import lifecycle

        provider = _make_provider()
        provider.create_pod = AsyncMock(
            side_effect=NoInstancesAvailableError("none available")
        )
        session_maker = _make_session_maker()

        with pytest.raises(NoInstancesAvailableError):
            await lifecycle.create_recorded_pod(
                provider,
                session_maker,
                spec=_make_spec(),
                idempotency_key="tts-abc",
                workflow_id=None,
                label="tts",
                gpu_type_fallbacks=["NVIDIA RTX 3090"],
            )

        assert provider.create_pod.await_count == 2  # primary + 1 fallback


# ---------------------------------------------------------------------------
# wait_for_recorded_ready
# ---------------------------------------------------------------------------

class TestWaitForRecordedReady:
    """Tests for lifecycle.wait_for_recorded_ready."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_pod_and_url(self) -> None:
        from app.gpu import lifecycle

        ready_pod = _make_pod(status=GpuPodStatus.READY, endpoint_url=_ENDPOINT)
        provider = _make_provider(wait_returns=ready_pod)
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCostService:
            cost_svc = AsyncMock()
            MockCostService.return_value = cost_svc

            pod, url = await lifecycle.wait_for_recorded_ready(
                provider,
                session_maker,
                _POD_ID,
                timeout_sec=120,
                label="tts",
                service_port=8080,
            )

        assert pod.id == _POD_ID
        assert url == _ENDPOINT
        cost_svc.mark_ready.assert_awaited_once_with(_POD_ID, _ENDPOINT)

    @pytest.mark.asyncio
    async def test_no_endpoint_url_raises(self) -> None:
        from app.gpu import lifecycle

        no_url_pod = _make_pod(endpoint_url=None)
        provider = _make_provider(wait_returns=no_url_pod)
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService"):
            with pytest.raises(RuntimeError, match="no endpoint_url"):
                await lifecycle.wait_for_recorded_ready(
                    provider,
                    session_maker,
                    _POD_ID,
                    timeout_sec=120,
                    label="tts",
                )


# ---------------------------------------------------------------------------
# terminate_and_finalize
# ---------------------------------------------------------------------------

class TestTerminateAndFinalize:
    """Tests for lifecycle.terminate_and_finalize."""

    @pytest.mark.asyncio
    async def test_happy_path_returns_cost(self) -> None:
        from app.gpu import lifecycle

        provider = _make_provider()
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCostService:
            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=150)
            MockCostService.return_value = cost_svc

            total = await lifecycle.terminate_and_finalize(
                provider, _POD_ID, session_maker
            )

        assert total == 150
        provider.terminate_pod.assert_awaited_once_with(_POD_ID)
        cost_svc.finalize_cost.assert_awaited_once_with(_POD_ID, reason="normal")

    @pytest.mark.asyncio
    async def test_custom_reason_passed_through(self) -> None:
        from app.gpu import lifecycle

        provider = _make_provider()
        session_maker = _make_session_maker()

        with patch.object(lifecycle, "CostService") as MockCostService:
            cost_svc = AsyncMock()
            cost_svc.finalize_cost = AsyncMock(return_value=0)
            MockCostService.return_value = cost_svc

            await lifecycle.terminate_and_finalize(
                provider, _POD_ID, session_maker, reason="cancelled"
            )

        cost_svc.finalize_cost.assert_awaited_once_with(_POD_ID, reason="cancelled")


# ---------------------------------------------------------------------------
# gpu_pod (async context manager)
# ---------------------------------------------------------------------------

class TestGpuPod:
    """Tests for the gpu_pod async context manager."""

    @pytest.mark.asyncio
    async def test_happy_path_yields_pod_and_url(self) -> None:
        """Happy path: create → wait → yield (pod, url) → terminate."""
        from app.gpu import lifecycle

        created_pod = _make_pod(pod_id="pod-1")
        ready_pod = _make_pod(pod_id="pod-1", status=GpuPodStatus.READY)

        with (
            patch.object(lifecycle, "create_recorded_pod", new=AsyncMock(return_value=created_pod)) as mock_create,
            patch.object(lifecycle, "wait_for_recorded_ready", new=AsyncMock(return_value=(ready_pod, _ENDPOINT))) as mock_wait,
            patch.object(lifecycle, "terminate_and_finalize", new=AsyncMock(return_value=100)) as mock_term,
        ):
            async with lifecycle.gpu_pod(
                MagicMock(spec=GpuProvider),
                _make_session_maker(),
                spec=_make_spec(),
                idempotency_key="tts-wf1",
                workflow_id=None,
                label="tts",
                timeout_sec=60,
                service_port=8080,
            ) as (pod, url):
                body_pod = pod
                body_url = url

        assert body_pod.id == "pod-1"
        assert body_url == _ENDPOINT
        mock_create.assert_awaited_once()
        mock_wait.assert_awaited_once()
        mock_term.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminate_called_on_work_body_exception(self) -> None:
        """Exception inside the `async with` body: terminate is still called."""
        from app.gpu import lifecycle

        created_pod = _make_pod()
        ready_pod = _make_pod(status=GpuPodStatus.READY)

        with (
            patch.object(lifecycle, "create_recorded_pod", new=AsyncMock(return_value=created_pod)),
            patch.object(lifecycle, "wait_for_recorded_ready", new=AsyncMock(return_value=(ready_pod, _ENDPOINT))),
            patch.object(lifecycle, "terminate_and_finalize", new=AsyncMock(return_value=50)) as mock_term,
        ):
            with pytest.raises(RuntimeError, match="work failed"):
                async with lifecycle.gpu_pod(
                    MagicMock(spec=GpuProvider),
                    _make_session_maker(),
                    spec=_make_spec(),
                    idempotency_key="tts-wf1",
                    workflow_id=None,
                    label="tts",
                    timeout_sec=60,
                ) as _:
                    raise RuntimeError("work failed")

        mock_term.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_terminate_called_on_wait_exception(self) -> None:
        """Exception from wait_for_recorded_ready: terminate is still called with create-pod ID."""
        from app.gpu import lifecycle

        created_pod = _make_pod(pod_id="pod-early")

        with (
            patch.object(lifecycle, "create_recorded_pod", new=AsyncMock(return_value=created_pod)),
            patch.object(lifecycle, "wait_for_recorded_ready", new=AsyncMock(side_effect=TimeoutError("timed out"))),
            patch.object(lifecycle, "terminate_and_finalize", new=AsyncMock(return_value=0)) as mock_term,
        ):
            with pytest.raises(TimeoutError):
                async with lifecycle.gpu_pod(
                    MagicMock(spec=GpuProvider),
                    _make_session_maker(),
                    spec=_make_spec(),
                    idempotency_key="k",
                    workflow_id=None,
                    label="tts",
                    timeout_sec=60,
                ):
                    pass  # pragma: no cover — never reached

        # terminate must be called even though we never entered the body
        mock_term.assert_awaited_once()
        _, call_pod_id, _ = mock_term.call_args.args
        assert call_pod_id == "pod-early"

    @pytest.mark.asyncio
    async def test_terminate_exception_is_swallowed(self) -> None:
        """Exception from terminate_and_finalize is swallowed; work body exception propagates."""
        from app.gpu import lifecycle

        created_pod = _make_pod()
        ready_pod = _make_pod(status=GpuPodStatus.READY)

        with (
            patch.object(lifecycle, "create_recorded_pod", new=AsyncMock(return_value=created_pod)),
            patch.object(lifecycle, "wait_for_recorded_ready", new=AsyncMock(return_value=(ready_pod, _ENDPOINT))),
            patch.object(lifecycle, "terminate_and_finalize", new=AsyncMock(side_effect=ConnectionError("runpod down"))),
        ):
            # No exception should escape: terminate error is swallowed
            async with lifecycle.gpu_pod(
                MagicMock(spec=GpuProvider),
                _make_session_maker(),
                spec=_make_spec(),
                idempotency_key="k",
                workflow_id=None,
                label="tts",
                timeout_sec=60,
            ) as (pod, url):
                result = url  # work succeeds

        assert result == _ENDPOINT  # reached without exception

    @pytest.mark.asyncio
    async def test_terminate_exception_does_not_mask_body_exception(self) -> None:
        """When both body and terminate raise, the body exception propagates (not terminate)."""
        from app.gpu import lifecycle

        created_pod = _make_pod()
        ready_pod = _make_pod(status=GpuPodStatus.READY)

        with (
            patch.object(lifecycle, "create_recorded_pod", new=AsyncMock(return_value=created_pod)),
            patch.object(lifecycle, "wait_for_recorded_ready", new=AsyncMock(return_value=(ready_pod, _ENDPOINT))),
            patch.object(lifecycle, "terminate_and_finalize", new=AsyncMock(side_effect=ConnectionError("runpod down"))),
        ):
            with pytest.raises(ValueError, match="body error"):
                async with lifecycle.gpu_pod(
                    MagicMock(spec=GpuProvider),
                    _make_session_maker(),
                    spec=_make_spec(),
                    idempotency_key="k",
                    workflow_id=None,
                    label="tts",
                    timeout_sec=60,
                ) as _:
                    raise ValueError("body error")

    @pytest.mark.asyncio
    async def test_gpu_type_fallbacks_forwarded(self) -> None:
        """gpu_type_fallbacks are passed through to create_recorded_pod."""
        from app.gpu import lifecycle

        created_pod = _make_pod()
        ready_pod = _make_pod(status=GpuPodStatus.READY)

        with (
            patch.object(lifecycle, "create_recorded_pod", new=AsyncMock(return_value=created_pod)) as mock_create,
            patch.object(lifecycle, "wait_for_recorded_ready", new=AsyncMock(return_value=(ready_pod, _ENDPOINT))),
            patch.object(lifecycle, "terminate_and_finalize", new=AsyncMock(return_value=0)),
        ):
            async with lifecycle.gpu_pod(
                MagicMock(spec=GpuProvider),
                _make_session_maker(),
                spec=_make_spec(),
                idempotency_key="k",
                workflow_id=None,
                label="tts",
                timeout_sec=60,
                gpu_type_fallbacks=["NVIDIA RTX 3090", "NVIDIA A100"],
            ):
                pass

        _, kwargs = mock_create.call_args.args, mock_create.call_args.kwargs
        assert mock_create.call_args.kwargs["gpu_type_fallbacks"] == ["NVIDIA RTX 3090", "NVIDIA A100"]

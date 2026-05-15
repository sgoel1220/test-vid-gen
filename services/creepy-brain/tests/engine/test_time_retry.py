"""Tests for time-based step retry, backoff, and StepDef mutual exclusion."""

from __future__ import annotations

import asyncio
import time
import warnings
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from app.engine.models import StepContext, StepDef

warnings.filterwarnings(
    "ignore",
    message="Unknown pytest.mark.asyncio.*",
    category=pytest.PytestUnknownMarkWarning,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# StepDef validation
# ---------------------------------------------------------------------------


class TestStepDefValidation:
    """Mutual exclusion: max_retries > 0 + retry_duration_sec raises."""

    def test_both_set_raises(self) -> None:
        with pytest.raises(ValidationError, match="Cannot set both"):
            StepDef(
                name="test",
                fn=AsyncMock(),
                max_retries=3,
                retry_duration_sec=60,
            )

    def test_duration_only_ok(self) -> None:
        s = StepDef(name="test", fn=AsyncMock(), retry_duration_sec=60)
        assert s.retry_duration_sec == 60
        assert s.max_retries == 0

    def test_retries_only_ok(self) -> None:
        s = StepDef(name="test", fn=AsyncMock(), max_retries=3)
        assert s.max_retries == 3
        assert s.retry_duration_sec is None

    def test_neither_ok(self) -> None:
        s = StepDef(name="test", fn=AsyncMock())
        assert s.max_retries == 0
        assert s.retry_duration_sec is None

    def test_backoff_defaults(self) -> None:
        s = StepDef(name="test", fn=AsyncMock(), retry_duration_sec=120)
        assert s.retry_backoff_sec == 5.0
        assert s.retry_backoff_max_sec == 60.0
        assert s.retry_backoff_strategy == "fixed"

    def test_exponential_strategy(self) -> None:
        s = StepDef(
            name="test",
            fn=AsyncMock(),
            retry_duration_sec=120,
            retry_backoff_strategy="exponential",
            retry_backoff_sec=2.0,
            retry_backoff_max_sec=30.0,
        )
        assert s.retry_backoff_strategy == "exponential"


# ---------------------------------------------------------------------------
# Time-based retry integration (execute_step)
# ---------------------------------------------------------------------------


class DummyOutput(BaseModel):
    ok: bool = True


class DummyLifecycle:
    def __init__(self) -> None:
        self._db_start_step = AsyncMock()
        self._db_complete_step = AsyncMock()
        self._db_fail_step = AsyncMock()


class DummyRunState:
    def missing_outputs(self, parents: list[str]) -> list[str]:
        return []

    def parent_outputs(self, parents: list[str]) -> dict[str, BaseModel]:
        return {}

    def record_output(self, name: str, output: BaseModel) -> None:
        pass


async def _make_executor() -> Any:
    """Build a WorkflowStepExecutor with mocked lifecycle and state."""
    from app.engine.runner import WorkflowStepExecutor
    lifecycle = DummyLifecycle()
    run_state = DummyRunState()
    # WorkflowStepExecutor expects these attributes
    executor = object.__new__(WorkflowStepExecutor)
    executor._workflow_id = "test-wf"  # type: ignore[attr-defined]
    executor._lifecycle = lifecycle  # type: ignore[attr-defined]
    executor._run_state = run_state  # type: ignore[attr-defined]
    return executor


class TestTimeBased:
    """Time-based retry loop in execute_step."""

    async def test_succeeds_first_try(self) -> None:
        executor = await _make_executor()
        step = StepDef(
            name="ok_step",
            fn=AsyncMock(return_value=DummyOutput()),
            retry_duration_sec=10,
        )
        result = await executor.execute_step(step, DummyOutput())
        assert result is None
        executor._lifecycle._db_complete_step.assert_called_once()

    async def test_fails_then_succeeds(self) -> None:
        call_count = 0

        async def flaky(inp: Any, ctx: Any) -> DummyOutput:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient")
            return DummyOutput()

        executor = await _make_executor()
        step = StepDef(
            name="flaky_step",
            fn=flaky,
            retry_duration_sec=30,
            retry_backoff_sec=0.01,  # fast for tests
        )
        result = await executor.execute_step(step, DummyOutput())
        assert result is None
        assert call_count == 3

    async def test_exhausts_time_budget(self) -> None:
        executor = await _make_executor()
        step = StepDef(
            name="always_fail",
            fn=AsyncMock(side_effect=RuntimeError("boom")),
            retry_duration_sec=0.1,  # very short
            retry_backoff_sec=0.01,
        )
        start = time.monotonic()
        result = await executor.execute_step(step, DummyOutput())
        elapsed = time.monotonic() - start
        assert result is not None  # error string
        assert "boom" in result
        # Should not have taken much more than 0.1s + overhead
        assert elapsed < 2.0

    async def test_cancellation_propagates(self) -> None:
        """CancelledError during step execution propagates immediately."""
        executor = await _make_executor()
        step = StepDef(
            name="cancel_step",
            fn=AsyncMock(side_effect=asyncio.CancelledError),
            retry_duration_sec=60,
            retry_backoff_sec=0.01,
        )
        with pytest.raises(asyncio.CancelledError):
            await executor.execute_step(step, DummyOutput())

    async def test_exponential_backoff(self) -> None:
        """Verify exponential backoff increases delay between attempts."""
        delays: list[float] = []
        original_sleep = asyncio.sleep

        async def mock_sleep(d: float) -> None:
            delays.append(d)
            # Don't actually sleep

        call_count = 0

        async def fail_n_times(inp: Any, ctx: Any) -> DummyOutput:
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                raise RuntimeError("fail")
            return DummyOutput()

        executor = await _make_executor()
        step = StepDef(
            name="exp_step",
            fn=fail_n_times,
            retry_duration_sec=300,
            retry_backoff_sec=1.0,
            retry_backoff_max_sec=10.0,
            retry_backoff_strategy="exponential",
        )
        with patch("asyncio.sleep", mock_sleep):
            result = await executor.execute_step(step, DummyOutput())
        assert result is None
        assert call_count == 5
        # Backoff delays: 1*2^0=1, 1*2^1=2, 1*2^2=4, 1*2^3=8
        assert len(delays) == 4
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)
        assert delays[3] == pytest.approx(8.0)


class TestCountBasedUnchanged:
    """Existing count-based retry is unaffected."""

    async def test_count_based_retries(self) -> None:
        call_count = 0

        async def fail_twice(inp: Any, ctx: Any) -> DummyOutput:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("fail")
            return DummyOutput()

        executor = await _make_executor()
        step = StepDef(
            name="count_step",
            fn=fail_twice,
            max_retries=3,
        )
        result = await executor.execute_step(step, DummyOutput())
        assert result is None
        assert call_count == 3

    async def test_count_based_exhausted(self) -> None:
        executor = await _make_executor()
        step = StepDef(
            name="always_fail",
            fn=AsyncMock(side_effect=RuntimeError("boom")),
            max_retries=2,
        )
        result = await executor.execute_step(step, DummyOutput())
        assert result is not None
        assert "boom" in result

    async def test_timeout_respected_in_time_based(self) -> None:
        """Per-attempt timeout_sec still applies within time-based retry."""
        executor = await _make_executor()

        async def slow_step(inp: Any, ctx: Any) -> DummyOutput:
            await asyncio.sleep(10)
            return DummyOutput()

        step = StepDef(
            name="slow",
            fn=slow_step,
            retry_duration_sec=0.5,
            timeout_sec=0.05,
            retry_backoff_sec=0.01,
        )
        result = await executor.execute_step(step, DummyOutput())
        assert result is not None
        assert "timed out" in result

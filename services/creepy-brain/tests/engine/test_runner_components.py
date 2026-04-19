"""Unit tests for WorkflowRunner behavior targeted by the planned decomposition."""

from __future__ import annotations

import asyncio
import logging
import warnings
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.engine import runner as runner_module
from app.engine.models import EmptyStepOutput, PauseAfterStep, StepContext, StepDef, WorkflowDef
from app.engine.runner import WorkflowRunner, _topo_sort, get_downstream_steps
from app.models.enums import StepName, StepStatus
from app.models.json_schemas import GenerateStoryStepOutput, TtsSynthesisStepOutput

warnings.filterwarnings(
    "ignore",
    message="Unknown pytest.mark.asyncio.*",
    category=pytest.PytestUnknownMarkWarning,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummyInput(BaseModel):
    value: str = "input"


class DummyOutput(BaseModel):
    value: str


def _dummy_output(value: str = "ok") -> DummyOutput:
    return DummyOutput(value=value)


def _story_output(title: str = "Story") -> GenerateStoryStepOutput:
    return GenerateStoryStepOutput(
        story_id=uuid.uuid4(),
        title=title,
        word_count=100,
        act_count=1,
    )


def _tts_output(gpu_pod_id: str = "pod-1") -> TtsSynthesisStepOutput:
    return TtsSynthesisStepOutput(
        run_id=uuid.uuid4(),
        chunk_count=3,
        total_duration_sec=12.5,
        gpu_pod_id=gpu_pod_id,
    )


def _step_mock(
    output: BaseModel,
    contexts: list[StepContext] | None = None,
) -> AsyncMock:
    async def _impl(workflow_input: object, ctx: StepContext) -> BaseModel:
        if contexts is not None:
            contexts.append(ctx)
        return output

    return AsyncMock(side_effect=_impl)


def _raising_step(exc: Exception) -> AsyncMock:
    async def _impl(workflow_input: object, ctx: StepContext) -> BaseModel:
        raise exc

    return AsyncMock(side_effect=_impl)


def _step(
    name: str,
    *,
    parents: list[str] | None = None,
    fn: AsyncMock | None = None,
    output: BaseModel | None = None,
    timeout_sec: float = 30.0,
    max_retries: int = 0,
    is_on_failure: bool = False,
    auto_pause_after: bool = False,
) -> StepDef:
    return StepDef(
        name=name,
        fn=fn or _step_mock(output or _dummy_output(name)),
        parents=parents or [],
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        is_on_failure=is_on_failure,
        auto_pause_after=auto_pause_after,
    )


def _workflow(steps: list[StepDef], name: str = "TestWorkflow") -> WorkflowDef:
    return WorkflowDef(name=name, steps=steps)


def _runner(
    workflow_def: WorkflowDef | None = None,
    *,
    completed_outputs: dict[str, BaseModel] | None = None,
) -> WorkflowRunner:
    return WorkflowRunner(
        workflow_def or _workflow([]),
        DummyInput(),
        uuid.uuid4(),
        completed_outputs=completed_outputs,
    )


def _result_with_rows(rows: list[object]) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = rows
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _workflow_step_row(
    step_name: StepName,
    output: BaseModel | None,
    *,
    attempt_number: int,
    status: StepStatus = StepStatus.COMPLETED,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_name=step_name,
        output_json=output,
        attempt_number=attempt_number,
        status=status,
    )


def _optional_session_for(session: object) -> Any:
    @asynccontextmanager
    async def _optional_session() -> AsyncIterator[object]:
        yield session

    return _optional_session


def _patch_db(session: object, service: MagicMock) -> Any:
    return patch.multiple(
        runner_module,
        optional_session=_optional_session_for(session),
        WorkflowService=MagicMock(return_value=service),
    )


def _service_mock() -> MagicMock:
    service = MagicMock()
    service.start_step = AsyncMock()
    service.complete_step = AsyncMock()
    service.fail_step = AsyncMock()
    service.fail_workflow = AsyncMock()
    return service


def _patch_runner_db_methods(runner: WorkflowRunner) -> None:
    runner._db_start_step = AsyncMock()  # type: ignore[method-assign]
    runner._db_complete_step = AsyncMock()  # type: ignore[method-assign]
    runner._db_fail_step = AsyncMock()  # type: ignore[method-assign]


class TestWorkflowDagPlanner:
    def test_topo_sort_valid_dag_returns_dependencies_before_dependents(self) -> None:
        steps = [
            _step("root"),
            _step("branch_a", parents=["root"]),
            _step("branch_b", parents=["root"]),
            _step("leaf", parents=["branch_a", "branch_b"]),
            _step("failure_cleanup", is_on_failure=True),
        ]

        ordered = _topo_sort("wf", steps)
        ordered_names = [step.name for step in ordered]

        assert ordered_names.index("root") < ordered_names.index("branch_a")
        assert ordered_names.index("root") < ordered_names.index("branch_b")
        assert ordered_names.index("branch_a") < ordered_names.index("leaf")
        assert ordered_names.index("branch_b") < ordered_names.index("leaf")
        assert "failure_cleanup" not in ordered_names

    def test_topo_sort_detects_cycle(self) -> None:
        steps = [
            _step("a", parents=["c"]),
            _step("b", parents=["a"]),
            _step("c", parents=["b"]),
        ]

        with pytest.raises(ValueError, match="cycle"):
            _topo_sort("wf", steps)

    def test_topo_sort_raises_on_unknown_parent_reference(self) -> None:
        steps = [_step("child", parents=["missing_parent"])]

        with pytest.raises(ValueError, match="unknown parent 'missing_parent'"):
            _topo_sort("wf", steps)

    def test_get_downstream_steps_returns_transitive_closure_including_self(self) -> None:
        steps = [
            _step("root"),
            _step("branch_a", parents=["root"]),
            _step("branch_b", parents=["root"]),
            _step("leaf", parents=["branch_a", "branch_b"]),
            _step("failure_cleanup", parents=["leaf"], is_on_failure=True),
        ]

        assert get_downstream_steps(steps, "root") == {
            "root",
            "branch_a",
            "branch_b",
            "leaf",
        }


class TestWorkflowRunState:
    def test_get_outputs_returns_defensive_copy(self) -> None:
        output = _dummy_output("completed")
        runner = _runner(completed_outputs={"step_a": output})

        returned = runner.get_outputs()
        returned["step_b"] = _dummy_output("mutated")
        del returned["step_a"]

        assert runner.get_outputs() == {"step_a": output}

    @pytest.mark.asyncio
    async def test_accumulates_output_from_multiple_steps(self) -> None:
        parent_output = _dummy_output("parent")
        child_output = _dummy_output("child")
        parent = _step("parent", output=parent_output)
        child = _step("child", parents=["parent"], output=child_output)
        runner = _runner(_workflow([parent, child]))
        _patch_runner_db_methods(runner)

        assert await runner._execute_step(parent) is None
        assert await runner._execute_step(child) is None

        outputs = runner.get_outputs()
        assert outputs["parent"] is parent_output
        assert outputs["child"] is child_output


class TestCompletedStepLoader:
    @pytest.mark.asyncio
    async def test_load_completed_steps_queries_db_and_hydrates_latest_outputs(self) -> None:
        story_output = _story_output()
        rows = [
            _workflow_step_row(
                StepName.GENERATE_STORY,
                story_output,
                attempt_number=2,
            ),
            _workflow_step_row(
                StepName.IMAGE_GENERATION,
                None,
                attempt_number=1,
            ),
        ]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_with_rows(rows))
        runner = _runner()

        with patch(
            "app.engine.runner.optional_session",
            _optional_session_for(session),
        ):
            await runner._load_completed_steps()

        session.execute.assert_awaited_once()
        outputs = runner.get_outputs()
        assert outputs[StepName.GENERATE_STORY.value] is story_output
        assert isinstance(outputs[StepName.IMAGE_GENERATION.value], EmptyStepOutput)

    @pytest.mark.asyncio
    async def test_load_completed_steps_skips_non_latest_attempts(self) -> None:
        latest_output = _story_output("latest")
        stale_output = _story_output("stale")
        rows = [
            _workflow_step_row(
                StepName.GENERATE_STORY,
                latest_output,
                attempt_number=2,
            ),
            _workflow_step_row(
                StepName.GENERATE_STORY,
                stale_output,
                attempt_number=1,
            ),
        ]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_with_rows(rows))
        runner = _runner()

        with patch(
            "app.engine.runner.optional_session",
            _optional_session_for(session),
        ):
            await runner._load_completed_steps()

        executed_stmt = session.execute.await_args.args[0]
        compiled_sql = str(executed_stmt.compile(compile_kwargs={"literal_binds": False}))
        assert "max(workflow_steps.attempt_number)" in compiled_sql
        assert "max_attempt" in compiled_sql
        assert runner.get_outputs()[StepName.GENERATE_STORY.value] is latest_output


class TestStepLifecycleRepository:
    @pytest.mark.asyncio
    async def test_db_start_step_calls_service_and_swallows_errors(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = AsyncMock()
        service = _service_mock()
        service.start_step.side_effect = RuntimeError("db down")
        runner = _runner()

        with _patch_db(session, service), caplog.at_level(logging.ERROR):
            await runner._db_start_step(StepName.GENERATE_STORY.value)

        service.start_step.assert_awaited_once_with(
            runner._workflow_id,
            StepName.GENERATE_STORY,
        )
        assert "_db_start_step" in caplog.text
        assert "db down" in caplog.text

    @pytest.mark.asyncio
    async def test_db_complete_step_calls_service_with_serialized_output_and_swallows_errors(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = AsyncMock()
        service = _service_mock()
        service.complete_step.side_effect = RuntimeError("db down")
        runner = _runner()
        output = _story_output()

        with _patch_db(session, service), caplog.at_level(logging.ERROR):
            await runner._db_complete_step(StepName.GENERATE_STORY.value, output)

        service.complete_step.assert_awaited_once_with(
            runner._workflow_id,
            StepName.GENERATE_STORY,
            output=output,
        )
        assert "_db_complete_step" in caplog.text
        assert "db down" in caplog.text

    @pytest.mark.asyncio
    async def test_db_fail_step_calls_service_with_error_string_and_swallows_errors(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = AsyncMock()
        service = _service_mock()
        service.fail_step.side_effect = RuntimeError("db down")
        runner = _runner()

        with _patch_db(session, service), caplog.at_level(logging.ERROR):
            await runner._db_fail_step(StepName.TTS_SYNTHESIS.value, "boom")

        service.fail_step.assert_awaited_once_with(
            runner._workflow_id,
            StepName.TTS_SYNTHESIS,
            "boom",
        )
        assert "_db_fail_step" in caplog.text
        assert "db down" in caplog.text

    @pytest.mark.asyncio
    async def test_fail_workflow_calls_service_fail_workflow(self) -> None:
        session = AsyncMock()
        service = _service_mock()
        runner = _runner()

        with _patch_db(session, service):
            await runner._fail_workflow("invalid dag")

        service.fail_workflow.assert_awaited_once_with(
            runner._workflow_id,
            "invalid dag",
        )
        session.commit.assert_awaited_once()


class TestWorkflowStepExecutor:
    @pytest.mark.asyncio
    async def test_execute_step_success_runs_fn_validates_output_and_records_completion(self) -> None:
        output = _story_output()
        contexts: list[StepContext] = []
        step_fn = _step_mock(output, contexts)
        step = _step(StepName.GENERATE_STORY.value, fn=step_fn)
        runner = _runner(_workflow([step]))
        _patch_runner_db_methods(runner)

        error = await runner._execute_step(step)

        assert error is None
        step_fn.assert_awaited_once()
        assert contexts[0].workflow_run_id == str(runner._workflow_id)
        assert contexts[0].parent_outputs == {}
        assert runner.get_outputs()[StepName.GENERATE_STORY.value] is output
        runner._db_start_step.assert_awaited_once_with(step.name)
        runner._db_complete_step.assert_awaited_once_with(step.name, output)
        runner._db_fail_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_step_step_fn_raises_records_failure(self) -> None:
        exc = RuntimeError("bad step")
        step = _step(StepName.GENERATE_STORY.value, fn=_raising_step(exc))
        runner = _runner(_workflow([step]))
        _patch_runner_db_methods(runner)

        error = await runner._execute_step(step)

        assert error == "Step 'generate_story' failed: bad step"
        runner._db_fail_step.assert_awaited_once_with(step.name, error)
        assert step.name not in runner.get_outputs()

    @pytest.mark.asyncio
    async def test_execute_step_missing_parent_output_fails_immediately(self) -> None:
        step = _step(StepName.TTS_SYNTHESIS.value, parents=[StepName.GENERATE_STORY.value])
        runner = _runner(_workflow([step]))
        _patch_runner_db_methods(runner)

        error = await runner._execute_step(step)

        assert error is not None
        assert "missing parent outputs" in error
        runner._db_start_step.assert_not_awaited()
        runner._db_complete_step.assert_not_awaited()
        runner._db_fail_step.assert_awaited_once_with(step.name, error)

    @pytest.mark.asyncio
    async def test_execute_step_timeout_records_failure(self) -> None:
        step = _step(
            StepName.GENERATE_STORY.value,
            timeout_sec=0.5,
            output=_story_output(),
        )
        runner = _runner(_workflow([step]))
        _patch_runner_db_methods(runner)

        async def _timeout(awaitable: object, *, timeout: float) -> BaseModel:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise asyncio.TimeoutError

        with patch(
            "app.engine.runner.asyncio.wait_for",
            new=AsyncMock(side_effect=_timeout),
        ) as wait_for:
            error = await runner._execute_step(step)

        assert error == "Step 'generate_story' timed out after 0.5s"
        wait_for.assert_awaited_once()
        runner._db_fail_step.assert_awaited_once_with(step.name, error)
        assert step.name not in runner.get_outputs()


class TestFailureStepRunner:
    @pytest.mark.asyncio
    async def test_run_on_failure_steps_runs_marked_steps_with_runtime_context(self) -> None:
        contexts: list[StepContext] = []
        normal_step = _step("normal")
        failure_step = _step(
            "failure_cleanup",
            fn=_step_mock(_dummy_output("cleanup"), contexts),
            is_on_failure=True,
        )
        runner = _runner(_workflow([normal_step, failure_step]))

        await runner._run_on_failure_steps("primary failure")

        failure_step.fn.assert_awaited_once()
        normal_step.fn.assert_not_called()
        assert contexts[0].workflow_run_id == str(runner._workflow_id)
        assert contexts[0].parent_outputs == {}

    @pytest.mark.asyncio
    async def test_run_on_failure_steps_swallows_individual_step_errors(self) -> None:
        failing_step = _step(
            "failing_cleanup",
            fn=_raising_step(RuntimeError("cleanup failed")),
            is_on_failure=True,
        )
        succeeding_step = _step(
            "succeeding_cleanup",
            fn=_step_mock(_dummy_output("cleanup")),
            is_on_failure=True,
        )
        runner = _runner(_workflow([failing_step, succeeding_step]))

        await runner._run_on_failure_steps("primary failure")

        failing_step.fn.assert_awaited_once()
        succeeding_step.fn.assert_awaited_once()


class TestWorkflowRunnerRun:
    @pytest.mark.asyncio
    async def test_run_valid_dag_all_steps_succeed_end_to_end(self) -> None:
        story_output = _story_output()
        tts_output = _tts_output()
        story_contexts: list[StepContext] = []
        tts_contexts: list[StepContext] = []
        generate_story = _step(
            StepName.GENERATE_STORY.value,
            fn=_step_mock(story_output, story_contexts),
        )
        tts = _step(
            StepName.TTS_SYNTHESIS.value,
            parents=[StepName.GENERATE_STORY.value],
            fn=_step_mock(tts_output, tts_contexts),
        )
        runner = _runner(_workflow([tts, generate_story]))
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_with_rows([]))
        service = _service_mock()

        with _patch_db(session, service):
            await runner.run()

        assert runner.get_outputs() == {
            StepName.GENERATE_STORY.value: story_output,
            StepName.TTS_SYNTHESIS.value: tts_output,
        }
        generate_story.fn.assert_awaited_once()
        tts.fn.assert_awaited_once()
        assert tts_contexts[0].parent_outputs == {
            StepName.GENERATE_STORY.value: story_output,
        }
        assert service.start_step.await_args_list[0].args == (
            runner._workflow_id,
            StepName.GENERATE_STORY,
        )
        assert service.start_step.await_args_list[1].args == (
            runner._workflow_id,
            StepName.TTS_SYNTHESIS,
        )
        assert service.complete_step.await_count == 2
        service.fail_step.assert_not_awaited()
        service.fail_workflow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_resume_skips_completed_steps_loaded_from_db(self) -> None:
        story_output = _story_output()
        tts_output = _tts_output()
        generate_story = _step(
            StepName.GENERATE_STORY.value,
            fn=_step_mock(_story_output("should not run")),
        )
        tts_contexts: list[StepContext] = []
        tts = _step(
            StepName.TTS_SYNTHESIS.value,
            parents=[StepName.GENERATE_STORY.value],
            fn=_step_mock(tts_output, tts_contexts),
        )
        completed_rows = [
            _workflow_step_row(
                StepName.GENERATE_STORY,
                story_output,
                attempt_number=1,
            )
        ]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_with_rows(completed_rows))
        service = _service_mock()
        runner = _runner(_workflow([generate_story, tts]))

        with _patch_db(session, service):
            await runner.run()

        generate_story.fn.assert_not_called()
        tts.fn.assert_awaited_once()
        assert tts_contexts[0].parent_outputs == {
            StepName.GENERATE_STORY.value: story_output,
        }
        service.start_step.assert_awaited_once_with(
            runner._workflow_id,
            StepName.TTS_SYNTHESIS,
        )
        service.complete_step.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_invalid_dag_triggers_fail_workflow(self) -> None:
        cycle_a = _step(StepName.GENERATE_STORY.value, parents=[StepName.TTS_SYNTHESIS.value])
        cycle_b = _step(StepName.TTS_SYNTHESIS.value, parents=[StepName.GENERATE_STORY.value])
        runner = _runner(_workflow([cycle_a, cycle_b]))
        session = AsyncMock()
        service = _service_mock()

        with _patch_db(session, service):
            await runner.run()

        service.fail_workflow.assert_awaited_once()
        assert "DAG has a cycle" in service.fail_workflow.await_args.args[1]
        service.start_step.assert_not_awaited()
        service.complete_step.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_auto_pause_stops_after_pauseable_step(self) -> None:
        story_output = _story_output()
        tts_output = _tts_output()
        generate_story = _step(
            StepName.GENERATE_STORY.value,
            fn=_step_mock(story_output),
            auto_pause_after=True,
        )
        tts = _step(
            StepName.TTS_SYNTHESIS.value,
            parents=[StepName.GENERATE_STORY.value],
            fn=_step_mock(tts_output),
        )
        runner = _runner(_workflow([generate_story, tts]))
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_with_rows([]))
        service = _service_mock()

        with _patch_db(session, service), pytest.raises(PauseAfterStep) as exc_info:
            await runner.run()

        assert exc_info.value.step_name == StepName.GENERATE_STORY.value
        assert runner.get_outputs() == {StepName.GENERATE_STORY.value: story_output}
        generate_story.fn.assert_awaited_once()
        tts.fn.assert_not_called()
        service.start_step.assert_awaited_once_with(
            runner._workflow_id,
            StepName.GENERATE_STORY,
        )
        service.complete_step.assert_awaited_once()

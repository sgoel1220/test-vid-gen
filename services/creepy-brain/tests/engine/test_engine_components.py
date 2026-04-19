"""Unit tests for WorkflowEngine responsibilities."""

from __future__ import annotations

import asyncio
import inspect
import importlib
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from pydantic import BaseModel

from app.engine.engine import WorkflowEngine
from app.engine.models import StepDef, WorkflowDef
from app.models.enums import (
    StepName,
    StepStatus,
    WorkflowStatus,
    WorkflowType,
)

engine_module = importlib.import_module("app.engine.engine")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummyInput(BaseModel):
    prompt: str = "test prompt"


class DummyOutput(BaseModel):
    value: str = "ok"


async def _noop_step(_workflow_input: DummyInput, _ctx: object) -> DummyOutput:
    return DummyOutput()


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_exc_info: object) -> None:
        return None


def _workflow_def(name: str = "ContentPipeline") -> WorkflowDef:
    return WorkflowDef(
        name=name,
        steps=[
            StepDef(name=StepName.GENERATE_STORY.value, fn=_noop_step),
            StepDef(
                name=StepName.TTS_SYNTHESIS.value,
                fn=_noop_step,
                parents=[StepName.GENERATE_STORY.value],
            ),
            StepDef(
                name=StepName.STITCH_FINAL.value,
                fn=_noop_step,
                parents=[StepName.TTS_SYNTHESIS.value],
            ),
        ],
    )


def _scalar_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(values: list[Any]) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = values
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _fake_create_task(created_tasks: list[dict[str, Any]]) -> Any:
    def fake_create_task(coro: Any, *, name: str | None = None) -> MagicMock:
        if inspect.iscoroutine(coro):
            coro.close()
        task = MagicMock(name=name or "workflow-task")
        task.done.return_value = False
        created_tasks.append({"task": task, "name": name})
        return task

    return fake_create_task


class TestWorkflowDefinitionRegistry:
    def test_register_stores_workflow_definition_by_name(self) -> None:
        engine = WorkflowEngine()
        workflow_def = _workflow_def()

        engine.register(workflow_def)

        assert engine._registry[workflow_def.name] is workflow_def

    def test_workflow_type_to_name_maps_registered_type_and_rejects_unknown(self) -> None:
        assert (
            engine_module._workflow_type_to_name(WorkflowType.CONTENT_PIPELINE)
            == "ContentPipeline"
        )
        with pytest.raises(KeyError):
            engine_module._workflow_type_to_name("unknown")  # type: ignore[arg-type]


class TestWorkflowTaskSupervisor:
    async def test_trigger_creates_asyncio_task_and_returns_run_id(self) -> None:
        engine = WorkflowEngine()
        workflow_def = _workflow_def()
        workflow_input = DummyInput()
        workflow_id = uuid.uuid4()
        runner = MagicMock(name="runner")
        created_tasks: list[dict[str, Any]] = []
        engine.register(workflow_def)

        with (
            patch.object(engine_module, "WorkflowRunner", return_value=runner) as runner_cls,
            patch.object(
                engine_module.asyncio,
                "create_task",
                side_effect=_fake_create_task(created_tasks),
            ),
        ):
            run_id = await engine.trigger(workflow_def.name, workflow_input, workflow_id)

        assert run_id == str(workflow_id)
        runner_cls.assert_called_once_with(workflow_def, workflow_input, workflow_id)
        assert created_tasks[0]["name"] == f"workflow-{workflow_id}"
        assert engine._tasks[run_id] is created_tasks[0]["task"]
        assert engine._runners[run_id] is runner

    async def test_stop_cancels_all_tracked_tasks(self) -> None:
        engine = WorkflowEngine()
        engine._tasks = {"run-1": MagicMock(), "run-2": MagicMock()}
        engine._cancel_task = AsyncMock(name="_cancel_task")  # type: ignore[method-assign]

        await engine.stop()

        engine._cancel_task.assert_has_awaits(
            [
                call("run-1", mark_cancelled_in_db=True),
                call("run-2", mark_cancelled_in_db=True),
            ]
        )
        assert engine._cancel_task.await_count == 2

    async def test_cancel_task_cancels_awaits_and_optionally_marks_cancelled(self) -> None:
        engine = WorkflowEngine()
        marked_workflow_id = uuid.uuid4()
        unmarked_workflow_id = uuid.uuid4()
        marked_cleanup_ran = asyncio.Event()
        unmarked_cleanup_ran = asyncio.Event()

        async def wait_until_cancelled(cleanup_ran: asyncio.Event) -> None:
            try:
                await asyncio.Future()
            finally:
                cleanup_ran.set()

        marked_task = asyncio.create_task(wait_until_cancelled(marked_cleanup_ran))
        unmarked_task = asyncio.create_task(wait_until_cancelled(unmarked_cleanup_ran))
        engine._tasks = {
            str(marked_workflow_id): marked_task,
            str(unmarked_workflow_id): unmarked_task,
        }
        engine._mark_workflow_cancelled = AsyncMock(  # type: ignore[method-assign]
            name="_mark_workflow_cancelled"
        )
        await asyncio.sleep(0)

        await engine._cancel_task(str(marked_workflow_id), mark_cancelled_in_db=True)
        await engine._cancel_task(str(unmarked_workflow_id), mark_cancelled_in_db=False)

        assert marked_cleanup_ran.is_set()
        assert unmarked_cleanup_ran.is_set()
        assert marked_task.cancelled()
        assert unmarked_task.cancelled()
        assert engine._tasks == {}
        engine._mark_workflow_cancelled.assert_awaited_once_with(marked_workflow_id)

    async def test_run_and_cleanup_removes_task_after_runner_completes(self) -> None:
        engine = WorkflowEngine()
        run_id = "run-1"
        runner = MagicMock(name="runner")
        runner.run = AsyncMock(name="run")
        engine._tasks[run_id] = MagicMock(name="task")

        await engine._run_and_cleanup(runner, run_id)

        runner.run.assert_awaited_once_with()
        assert run_id not in engine._tasks


class TestWorkflowRetryResumeController:
    async def test_retry_step_hot_runner_resets_downstream_and_reschedules(self) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        run_id = str(workflow_id)
        workflow_def = _workflow_def()
        engine.register(workflow_def)
        existing_outputs = {
            StepName.GENERATE_STORY.value: DummyOutput(value="story"),
            StepName.TTS_SYNTHESIS.value: DummyOutput(value="tts"),
            StepName.STITCH_FINAL.value: DummyOutput(value="stitch"),
        }
        existing_runner = MagicMock(name="existing_runner")
        existing_runner.get_outputs.return_value = dict(existing_outputs)
        existing_runner._def = workflow_def
        existing_runner.workflow_input = DummyInput(prompt="resume me")
        new_runner = MagicMock(name="new_runner")
        created_tasks: list[dict[str, Any]] = []
        engine._tasks[run_id] = MagicMock(name="old_task")
        engine._runners[run_id] = existing_runner
        engine._cancel_task = AsyncMock(name="_cancel_task")  # type: ignore[method-assign]
        engine._reset_steps_in_db = AsyncMock(  # type: ignore[method-assign]
            name="_reset_steps_in_db"
        )
        engine._set_workflow_status_running = AsyncMock(  # type: ignore[method-assign]
            name="_set_workflow_status_running"
        )

        with (
            patch.object(engine_module, "WorkflowRunner", return_value=new_runner) as runner_cls,
            patch.object(
                engine_module.asyncio,
                "create_task",
                side_effect=_fake_create_task(created_tasks),
            ),
        ):
            await engine.retry_step(run_id, StepName.TTS_SYNTHESIS.value)

        engine._cancel_task.assert_awaited_once_with(
            run_id, mark_cancelled_in_db=False
        )
        engine._reset_steps_in_db.assert_awaited_once_with(
            workflow_id,
            {StepName.TTS_SYNTHESIS.value, StepName.STITCH_FINAL.value},
        )
        engine._set_workflow_status_running.assert_awaited_once_with(workflow_id)
        runner_cls.assert_called_once_with(
            workflow_def,
            existing_runner.workflow_input,
            workflow_id,
            {StepName.GENERATE_STORY.value: existing_outputs[StepName.GENERATE_STORY.value]},
        )
        assert engine._runners[run_id] is new_runner
        assert engine._tasks[run_id] is created_tasks[0]["task"]

    async def test_retry_step_without_hot_runner_loads_db_and_schedules_cold_resume(
        self,
    ) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        run_id = str(workflow_id)
        workflow_def = _workflow_def()
        wf_row = MagicMock()
        wf_row.workflow_type = WorkflowType.CONTENT_PIPELINE
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(wf_row))
        engine.register(workflow_def)
        engine._cancel_task = AsyncMock(name="_cancel_task")  # type: ignore[method-assign]
        engine._reset_steps_in_db = AsyncMock(  # type: ignore[method-assign]
            name="_reset_steps_in_db"
        )
        engine.resume_from_db = AsyncMock(  # type: ignore[method-assign]
            name="resume_from_db",
            return_value=run_id,
        )

        with patch.object(
            engine_module,
            "optional_session",
            return_value=_AsyncContext(session),
        ):
            await engine.retry_step(run_id, StepName.TTS_SYNTHESIS.value)

        engine._cancel_task.assert_awaited_once_with(
            run_id, mark_cancelled_in_db=False
        )
        session.execute.assert_awaited_once()
        engine._reset_steps_in_db.assert_awaited_once_with(
            workflow_id,
            {StepName.TTS_SYNTHESIS.value, StepName.STITCH_FINAL.value},
        )
        engine.resume_from_db.assert_awaited_once_with(workflow_id)

    async def test_resume_from_db_marks_running_builds_runner_and_schedules_task(
        self,
    ) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        run_id = str(workflow_id)
        workflow_def = _workflow_def()
        workflow_input = DummyInput(prompt="from db")
        wf_row = MagicMock()
        wf_row.workflow_type = WorkflowType.CONTENT_PIPELINE
        wf_row.input_json = workflow_input
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(wf_row))
        new_runner = MagicMock(name="resume_runner")
        created_tasks: list[dict[str, Any]] = []
        engine.register(workflow_def)
        engine._set_workflow_status = AsyncMock(  # type: ignore[method-assign]
            name="_set_workflow_status"
        )

        with (
            patch.object(
                engine_module,
                "optional_session",
                return_value=_AsyncContext(session),
            ),
            patch.object(engine_module, "WorkflowRunner", return_value=new_runner) as runner_cls,
            patch.object(
                engine_module.asyncio,
                "create_task",
                side_effect=_fake_create_task(created_tasks),
            ),
        ):
            result = await engine.resume_from_db(workflow_id)

        assert result == run_id
        session.execute.assert_awaited_once()
        engine._set_workflow_status.assert_awaited_once_with(
            workflow_id, WorkflowStatus.RUNNING
        )
        runner_cls.assert_called_once_with(workflow_def, workflow_input, workflow_id)
        assert engine._runners[run_id] is new_runner
        assert engine._tasks[run_id] is created_tasks[0]["task"]


class TestWorkflowStateRepository:
    async def test_reset_steps_in_db_sets_named_steps_back_to_pending(self) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        step = MagicMock()
        step.status = StepStatus.FAILED
        step.error = "failed"
        step.completed_at = datetime(2024, 1, 1)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(step))
        session.commit = AsyncMock()

        with patch.object(
            engine_module,
            "optional_session",
            return_value=_AsyncContext(session),
        ):
            await engine._reset_steps_in_db(
                workflow_id, {StepName.TTS_SYNTHESIS.value}
            )

        assert step.status == StepStatus.PENDING
        assert step.error is None
        assert step.completed_at is None
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once_with()

    async def test_set_workflow_status_running_skips_completed_and_sets_other_statuses(
        self,
    ) -> None:
        engine = WorkflowEngine()
        completed_workflow = MagicMock()
        completed_workflow.status = WorkflowStatus.COMPLETED
        failed_workflow = MagicMock()
        failed_workflow.status = WorkflowStatus.FAILED
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                _scalar_result(completed_workflow),
                _scalar_result(failed_workflow),
            ]
        )
        session.commit = AsyncMock()

        with patch.object(
            engine_module,
            "optional_session",
            return_value=_AsyncContext(session),
        ):
            await engine._set_workflow_status_running(uuid.uuid4())
            await engine._set_workflow_status_running(uuid.uuid4())

        assert completed_workflow.status == WorkflowStatus.COMPLETED
        assert failed_workflow.status == WorkflowStatus.RUNNING
        assert session.execute.await_count == 2
        assert session.commit.await_count == 2

    async def test_mark_workflow_cancelled_sets_cancelled_and_completed_at(self) -> None:
        engine = WorkflowEngine()
        workflow = MagicMock()
        workflow.status = WorkflowStatus.RUNNING
        workflow.completed_at = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(workflow))
        session.commit = AsyncMock()

        with patch.object(
            engine_module,
            "optional_session",
            return_value=_AsyncContext(session),
        ):
            await engine._mark_workflow_cancelled(uuid.uuid4())

        assert workflow.status == WorkflowStatus.CANCELLED
        assert isinstance(workflow.completed_at, datetime)
        assert workflow.completed_at.tzinfo is not None
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once_with()

    async def test_set_workflow_status_sets_arbitrary_status(self) -> None:
        engine = WorkflowEngine()
        workflow = MagicMock()
        workflow.status = WorkflowStatus.PENDING
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(workflow))
        session.commit = AsyncMock()

        with patch.object(
            engine_module,
            "optional_session",
            return_value=_AsyncContext(session),
        ):
            await engine._set_workflow_status(uuid.uuid4(), WorkflowStatus.PAUSED)

        assert workflow.status == WorkflowStatus.PAUSED
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once_with()


class TestWorkflowResourceController:
    async def test_pause_cancels_task_terminates_gpu_pods_and_marks_paused(self) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        run_id = str(workflow_id)
        engine._cancel_task = AsyncMock(name="_cancel_task")  # type: ignore[method-assign]
        engine._terminate_gpu_pods = AsyncMock(  # type: ignore[method-assign]
            name="_terminate_gpu_pods"
        )
        engine._set_workflow_status = AsyncMock(  # type: ignore[method-assign]
            name="_set_workflow_status"
        )

        await engine.pause(run_id)

        engine._cancel_task.assert_awaited_once_with(
            run_id, mark_cancelled_in_db=False
        )
        engine._terminate_gpu_pods.assert_awaited_once_with(workflow_id)
        engine._set_workflow_status.assert_awaited_once_with(
            workflow_id, WorkflowStatus.PAUSED
        )

    async def test_cancel_cancels_task_terminates_gpu_pods_and_marks_cancelled(
        self,
    ) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        run_id = str(workflow_id)
        engine._cancel_task = AsyncMock(name="_cancel_task")  # type: ignore[method-assign]
        engine._terminate_gpu_pods = AsyncMock(  # type: ignore[method-assign]
            name="_terminate_gpu_pods"
        )

        await engine.cancel(run_id)

        engine._cancel_task.assert_awaited_once_with(
            run_id, mark_cancelled_in_db=True
        )
        engine._terminate_gpu_pods.assert_awaited_once_with(workflow_id)

    async def test_terminate_gpu_pods_finds_active_pods_and_terminates_best_effort(
        self,
    ) -> None:
        engine = WorkflowEngine()
        workflow_id = uuid.uuid4()
        pod_a = MagicMock()
        pod_a.id = "pod-a"
        pod_b = MagicMock()
        pod_b.id = "pod-b"
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalars_result([pod_a, pod_b]))
        session_maker = MagicMock(return_value=_AsyncContext(session))
        provider = MagicMock(name="provider")
        terminate_and_finalize = AsyncMock(
            side_effect=[RuntimeError("pod-a failed"), None],
            name="terminate_and_finalize",
        )
        settings = SimpleNamespace(runpod_api_key="test-key")

        with (
            patch.object(
                engine_module,
                "get_optional_session_maker",
                return_value=session_maker,
            ),
            patch("app.config.settings", settings),
            patch("app.gpu.get_provider", return_value=provider) as get_provider,
            patch(
                "app.gpu.lifecycle.terminate_and_finalize",
                terminate_and_finalize,
            ),
        ):
            await engine._terminate_gpu_pods(workflow_id)

        session.execute.assert_awaited_once()
        get_provider.assert_called_once_with("test-key")
        terminate_and_finalize.assert_has_awaits(
            [
                call(provider, "pod-a", session_maker, reason="workflow_cancelled"),
                call(provider, "pod-b", session_maker, reason="workflow_cancelled"),
            ]
        )

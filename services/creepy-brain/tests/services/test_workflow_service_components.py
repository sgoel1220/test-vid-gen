"""Unit tests for the current workflow_service module.

The production module has one ``WorkflowService`` class plus module-level read
and fork helpers.  These tests map the planned component boundaries onto those
current methods so future refactors can keep behavior stable.
"""

from __future__ import annotations

import uuid
import warnings
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import ChunkStatus, StepName, StepStatus, WorkflowStatus, WorkflowType
from app.models.json_schemas import (
    GenerateStoryStepOutput,
    ImageGenerationStepOutput,
    TtsSynthesisStepOutput,
    WorkflowInputSchema,
    WorkflowResultSchema,
)
from app.models.workflow import Workflow, WorkflowChunk, WorkflowScene, WorkflowStep
from app.services.workflow_service import (
    WorkflowService,
    fork_workflow,
    get_chunks_for_image_step,
    get_scenes_for_workflow,
)

warnings.filterwarnings(
    "ignore",
    message="Unknown pytest.mark.asyncio.*",
    category=pytest.PytestUnknownMarkWarning,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _mock_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _scalar_result(value: object | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_result(values: Sequence[object]) -> MagicMock:
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(values)
    result.scalars.return_value = scalars
    return result


def _workflow(
    workflow_id: uuid.UUID,
    status: WorkflowStatus = WorkflowStatus.PENDING,
) -> Workflow:
    return Workflow(
        id=workflow_id,
        workflow_type=WorkflowType.CONTENT_PIPELINE,
        input_json=WorkflowInputSchema(
            premise="A haunted relay station",
            voice_name="old_man_low.wav",
        ),
        status=status,
    )


def _chunk(
    workflow_id: uuid.UUID,
    chunk_index: int,
    chunk_text: str = "chunk text",
    tts_status: ChunkStatus = ChunkStatus.PENDING,
    scene_id: uuid.UUID | None = None,
) -> WorkflowChunk:
    return WorkflowChunk(
        workflow_id=workflow_id,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        tts_status=tts_status,
        scene_id=scene_id,
    )


def _scene(
    workflow_id: uuid.UUID,
    scene_index: int,
    scene_id: uuid.UUID | None = None,
    image_status: ChunkStatus = ChunkStatus.PENDING,
) -> WorkflowScene:
    return WorkflowScene(
        id=scene_id,
        workflow_id=workflow_id,
        scene_index=scene_index,
        image_status=image_status,
    )


def _step(
    workflow_id: uuid.UUID,
    step_name: StepName,
    status: StepStatus = StepStatus.RUNNING,
    attempt_number: int = 1,
    output_json: Any = None,
) -> WorkflowStep:
    return WorkflowStep(
        workflow_id=workflow_id,
        step_name=step_name,
        status=status,
        attempt_number=attempt_number,
        output_json=output_json,
    )


class TestWorkflowChunkServiceMethods:
    async def test_upsert_chunk_creates_chunk_if_not_exists(self) -> None:
        workflow_id = uuid.uuid4()
        session = _mock_session()
        session.execute.return_value = _scalar_result(None)

        service = WorkflowService(session)

        chunk = await service.upsert_chunk(workflow_id, 2, "new text")

        assert chunk.workflow_id == workflow_id
        assert chunk.chunk_index == 2
        assert chunk.chunk_text == "new text"
        assert chunk.tts_status == ChunkStatus.PENDING
        session.add.assert_called_once_with(chunk)
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(chunk)

    async def test_upsert_chunk_updates_text_and_resets_tts_state_when_text_changes(
        self,
    ) -> None:
        workflow_id = uuid.uuid4()
        old_blob_id = uuid.uuid4()
        existing = _chunk(
            workflow_id,
            chunk_index=3,
            chunk_text="old text",
            tts_status=ChunkStatus.COMPLETED,
        )
        existing.tts_audio_blob_id = old_blob_id
        existing.tts_duration_sec = 12.5

        session = _mock_session()
        session.execute.return_value = _scalar_result(existing)

        service = WorkflowService(session)

        chunk = await service.upsert_chunk(workflow_id, 3, "new text")

        assert chunk is existing
        assert existing.chunk_text == "new text"
        assert existing.tts_status == ChunkStatus.PENDING
        assert existing.tts_audio_blob_id is None
        assert existing.tts_duration_sec is None
        session.add.assert_not_called()
        session.flush.assert_awaited_once()
        session.refresh.assert_not_awaited()

    async def test_upsert_chunk_no_ops_when_text_unchanged(self) -> None:
        workflow_id = uuid.uuid4()
        blob_id = uuid.uuid4()
        existing = _chunk(
            workflow_id,
            chunk_index=4,
            chunk_text="same text",
            tts_status=ChunkStatus.COMPLETED,
        )
        existing.tts_audio_blob_id = blob_id
        existing.tts_duration_sec = 8.0

        session = _mock_session()
        session.execute.return_value = _scalar_result(existing)

        service = WorkflowService(session)

        chunk = await service.upsert_chunk(workflow_id, 4, "same text")

        assert chunk is existing
        assert existing.tts_status == ChunkStatus.COMPLETED
        assert existing.tts_audio_blob_id == blob_id
        assert existing.tts_duration_sec == 8.0
        session.add.assert_not_called()
        session.flush.assert_not_awaited()
        session.refresh.assert_not_awaited()

    async def test_mark_chunk_processing_updates_status_to_processing(self) -> None:
        workflow_id = uuid.uuid4()
        chunk = _chunk(workflow_id, 1)
        session = _mock_session()
        session.execute.return_value = _scalar_result(chunk)

        service = WorkflowService(session)

        await service.mark_chunk_processing(workflow_id, 1)

        assert chunk.tts_status == ChunkStatus.PROCESSING
        session.flush.assert_awaited_once()

    async def test_complete_chunk_tts_marks_complete_with_audio_metadata(self) -> None:
        workflow_id = uuid.uuid4()
        wav_blob_id = uuid.uuid4()
        mp3_blob_id = uuid.uuid4()
        chunk = _chunk(workflow_id, 1, tts_status=ChunkStatus.PROCESSING)
        session = _mock_session()
        session.execute.return_value = _scalar_result(chunk)

        service = WorkflowService(session)

        await service.complete_chunk_tts(
            workflow_id,
            chunk_index=1,
            blob_id=wav_blob_id,
            duration_sec=3.75,
            attempts_used=2,
            mp3_blob_id=mp3_blob_id,
        )

        assert chunk.tts_status == ChunkStatus.COMPLETED
        assert chunk.tts_audio_blob_id == wav_blob_id
        assert chunk.tts_mp3_blob_id == mp3_blob_id
        assert chunk.tts_duration_sec == 3.75
        assert chunk.tts_completed_at is not None
        session.flush.assert_awaited_once()

    async def test_fail_chunk_tts_marks_failed_and_retains_wav_blob(self) -> None:
        workflow_id = uuid.uuid4()
        wav_blob_id = uuid.uuid4()
        chunk = _chunk(workflow_id, 1, tts_status=ChunkStatus.PROCESSING)
        session = _mock_session()
        session.execute.return_value = _scalar_result(chunk)

        service = WorkflowService(session)

        await service.fail_chunk_tts(
            workflow_id,
            chunk_index=1,
            blob_id=wav_blob_id,
            attempts_used=3,
        )

        assert chunk.tts_status == ChunkStatus.FAILED
        assert chunk.tts_audio_blob_id == wav_blob_id
        assert chunk.tts_completed_at is not None
        session.flush.assert_awaited_once()

    async def test_reset_chunks_to_pending_resets_failed_chunks_and_clears_audio_metadata(
        self,
    ) -> None:
        workflow_id = uuid.uuid4()
        failed_one = _chunk(workflow_id, 1, tts_status=ChunkStatus.FAILED)
        failed_one.tts_audio_blob_id = uuid.uuid4()
        failed_one.tts_mp3_blob_id = uuid.uuid4()
        failed_one.tts_completed_at = datetime.now(timezone.utc)
        failed_two = _chunk(workflow_id, 2, tts_status=ChunkStatus.FAILED)
        failed_two.tts_audio_blob_id = uuid.uuid4()
        failed_two.tts_mp3_blob_id = uuid.uuid4()
        failed_two.tts_completed_at = datetime.now(timezone.utc)

        session = _mock_session()
        session.execute.return_value = _scalars_result([failed_one, failed_two])

        service = WorkflowService(session)

        count = await service.reset_chunks_to_pending(workflow_id, chunk_indices=[1, 2])

        assert count == 2
        for chunk in (failed_one, failed_two):
            assert chunk.tts_status == ChunkStatus.PENDING
            assert chunk.tts_audio_blob_id is None
            assert chunk.tts_mp3_blob_id is None
            assert chunk.tts_completed_at is None
        session.flush.assert_awaited_once()


class TestWorkflowSceneServiceMethods:
    async def test_create_scene_creates_scene_and_links_selected_chunks(self) -> None:
        workflow_id = uuid.uuid4()
        scene_id = uuid.uuid4()
        chunk_zero = _chunk(workflow_id, 0)
        chunk_two = _chunk(workflow_id, 2)
        session = _mock_session()
        session.execute.side_effect = [
            _scalar_result(chunk_zero),
            _scalar_result(chunk_two),
        ]

        def assign_scene_id(scene: WorkflowScene) -> None:
            scene.id = scene_id

        session.refresh.side_effect = assign_scene_id
        service = WorkflowService(session)

        scene = await service.create_scene(workflow_id, scene_index=5, chunk_indices=[0, 2])

        assert scene.workflow_id == workflow_id
        assert scene.scene_index == 5
        assert scene.image_status == ChunkStatus.PENDING
        assert chunk_zero.scene_id == scene_id
        assert chunk_two.scene_id == scene_id
        session.add.assert_called_once_with(scene)
        assert session.flush.await_count == 2
        session.refresh.assert_awaited_once_with(scene)

    async def test_get_or_create_scene_returns_existing_scene_if_found(self) -> None:
        workflow_id = uuid.uuid4()
        existing = _scene(workflow_id, scene_index=1, scene_id=uuid.uuid4())
        session = _mock_session()
        session.execute.return_value = _scalar_result(existing)

        service = WorkflowService(session)

        scene = await service.get_or_create_scene(workflow_id, 1, chunk_indices=[0, 1])

        assert scene is existing
        session.add.assert_not_called()
        session.flush.assert_not_awaited()
        session.refresh.assert_not_awaited()

    async def test_get_or_create_scene_creates_if_not_found(self) -> None:
        workflow_id = uuid.uuid4()
        scene_id = uuid.uuid4()
        chunk_zero = _chunk(workflow_id, 0)
        chunk_one = _chunk(workflow_id, 1)
        session = _mock_session()
        session.execute.side_effect = [
            _scalar_result(None),
            _scalar_result(chunk_zero),
            _scalar_result(chunk_one),
        ]

        def assign_scene_id(scene: WorkflowScene) -> None:
            scene.id = scene_id

        session.refresh.side_effect = assign_scene_id
        service = WorkflowService(session)

        scene = await service.get_or_create_scene(workflow_id, 2, chunk_indices=[0, 1])

        assert scene.scene_index == 2
        assert chunk_zero.scene_id == scene_id
        assert chunk_one.scene_id == scene_id
        session.add.assert_called_once_with(scene)
        assert session.flush.await_count == 2

    async def test_save_scene_prompt_saves_positive_and_negative_prompts(self) -> None:
        workflow_id = uuid.uuid4()
        scene = _scene(workflow_id, scene_index=1, scene_id=uuid.uuid4())
        session = _mock_session()
        session.execute.return_value = _scalar_result(scene)

        service = WorkflowService(session)

        await service.save_scene_prompt(
            workflow_id,
            1,
            image_prompt="moonlit station",
            image_negative_prompt="low quality",
        )

        assert scene.image_prompt == "moonlit station"
        assert scene.image_negative_prompt == "low quality"
        session.flush.assert_awaited_once()

    async def test_complete_scene_image_marks_complete_with_image_blob(self) -> None:
        workflow_id = uuid.uuid4()
        image_blob_id = uuid.uuid4()
        scene = _scene(workflow_id, scene_index=1, scene_id=uuid.uuid4())
        session = _mock_session()
        session.execute.return_value = _scalar_result(scene)

        service = WorkflowService(session)

        await service.complete_scene_image(workflow_id, 1, image_blob_id)

        assert scene.image_status == ChunkStatus.COMPLETED
        assert scene.image_blob_id == image_blob_id
        assert scene.image_completed_at is not None
        session.flush.assert_awaited_once()


class TestWorkflowStepServiceMethods:
    async def test_start_step_creates_new_step_attempt_and_updates_parent_workflow(
        self,
    ) -> None:
        workflow_id = uuid.uuid4()
        latest = _step(
            workflow_id,
            StepName.TTS_SYNTHESIS,
            status=StepStatus.COMPLETED,
            attempt_number=2,
        )
        workflow = _workflow(workflow_id, status=WorkflowStatus.PENDING)
        session = _mock_session()
        session.execute.side_effect = [
            _scalar_result(latest),
            _scalar_result(workflow),
        ]
        service = WorkflowService(session)

        await service.start_step(workflow_id, StepName.TTS_SYNTHESIS)

        added_step = session.add.call_args.args[0]
        assert isinstance(added_step, WorkflowStep)
        assert added_step.workflow_id == workflow_id
        assert added_step.step_name == StepName.TTS_SYNTHESIS
        assert added_step.status == StepStatus.RUNNING
        assert added_step.attempt_number == 3
        assert added_step.started_at is not None
        assert workflow.current_step == StepName.TTS_SYNTHESIS
        assert workflow.status == WorkflowStatus.RUNNING
        assert workflow.started_at is not None
        session.flush.assert_awaited_once()

    async def test_complete_step_marks_running_step_complete_with_output(self) -> None:
        workflow_id = uuid.uuid4()
        output = GenerateStoryStepOutput(
            story_id=uuid.uuid4(),
            title="Signal",
            word_count=500,
            act_count=3,
        )
        step = _step(workflow_id, StepName.GENERATE_STORY)
        session = _mock_session()
        session.execute.return_value = _scalar_result(step)
        service = WorkflowService(session)

        await service.complete_step(workflow_id, StepName.GENERATE_STORY, output=output)

        assert step.status == StepStatus.COMPLETED
        assert step.output_json is output
        assert step.completed_at is not None
        session.flush.assert_awaited_once()

    async def test_fail_step_marks_running_step_failed_with_error_string(self) -> None:
        workflow_id = uuid.uuid4()
        step = _step(workflow_id, StepName.IMAGE_GENERATION)
        session = _mock_session()
        session.execute.return_value = _scalar_result(step)
        service = WorkflowService(session)

        await service.fail_step(workflow_id, StepName.IMAGE_GENERATION, "gpu unavailable")

        assert step.status == StepStatus.FAILED
        assert step.error == "gpu unavailable"
        assert step.completed_at is not None
        session.flush.assert_awaited_once()


class TestWorkflowLifecycleServiceMethods:
    async def test_complete_workflow_marks_complete_with_result_json(self) -> None:
        workflow_id = uuid.uuid4()
        workflow = _workflow(workflow_id, status=WorkflowStatus.RUNNING)
        result = WorkflowResultSchema(
            story_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            total_duration_sec=64.5,
            chunk_count=8,
        )
        session = _mock_session()
        session.execute.return_value = _scalar_result(workflow)
        service = WorkflowService(session)

        await service.complete_workflow(workflow_id, result)

        assert workflow.status == WorkflowStatus.COMPLETED
        assert workflow.result_json is result
        assert workflow.completed_at is not None
        session.flush.assert_awaited_once()

    async def test_fail_workflow_marks_failed_with_error_string(self) -> None:
        workflow_id = uuid.uuid4()
        workflow = _workflow(workflow_id, status=WorkflowStatus.RUNNING)
        session = _mock_session()
        session.execute.return_value = _scalar_result(workflow)
        service = WorkflowService(session)

        await service.fail_workflow(workflow_id, "story generation failed")

        assert workflow.status == WorkflowStatus.FAILED
        assert workflow.error == "story generation failed"
        assert workflow.completed_at is not None
        session.flush.assert_awaited_once()


class TestWorkflowReadRepositoryHelpers:
    async def test_get_chunks_for_image_step_returns_ordered_chunk_projections(
        self,
    ) -> None:
        workflow_id = uuid.uuid4()
        scene_id = uuid.uuid4()
        wav_blob_id = uuid.uuid4()
        chunk_zero = _chunk(
            workflow_id,
            chunk_index=0,
            chunk_text="first",
            tts_status=ChunkStatus.COMPLETED,
            scene_id=scene_id,
        )
        chunk_zero.tts_audio_blob_id = wav_blob_id
        chunk_zero.tts_duration_sec = 1.25
        chunk_one = _chunk(
            workflow_id,
            chunk_index=1,
            chunk_text="second",
            tts_status=ChunkStatus.FAILED,
        )

        session = _mock_session()
        session.execute.return_value = _scalars_result([chunk_zero, chunk_one])

        chunks = await get_chunks_for_image_step(session, workflow_id)

        assert [chunk.index for chunk in chunks] == [0, 1]
        assert chunks[0].text == "first"
        assert chunks[0].blob_id == str(wav_blob_id)
        assert chunks[0].tts_status == ChunkStatus.COMPLETED
        assert chunks[0].scene_id == str(scene_id)
        assert chunks[0].duration_sec == 1.25
        assert chunks[1].text == "second"
        assert chunks[1].blob_id is None
        assert chunks[1].tts_status == ChunkStatus.FAILED
        assert chunks[1].scene_id is None

    async def test_get_scenes_for_workflow_returns_scenes_ordered_by_index(self) -> None:
        workflow_id = uuid.uuid4()
        scene_zero = _scene(workflow_id, scene_index=0, scene_id=uuid.uuid4())
        scene_one = _scene(workflow_id, scene_index=1, scene_id=uuid.uuid4())
        session = _mock_session()
        session.execute.return_value = _scalars_result([scene_zero, scene_one])

        scenes = await get_scenes_for_workflow(session, workflow_id)

        assert scenes == [scene_zero, scene_one]


class TestWorkflowForkServiceHelper:
    async def test_fork_workflow_creates_fork_seeds_steps_and_copies_chunks_and_scenes(
        self,
    ) -> None:
        source_id = uuid.uuid4()
        new_workflow_id = uuid.uuid4()
        old_scene_id = uuid.uuid4()
        new_scene_id = uuid.uuid4()
        image_blob_id = uuid.uuid4()
        wav_blob_id = uuid.uuid4()
        mp3_blob_id = uuid.uuid4()

        source_workflow = _workflow(source_id, status=WorkflowStatus.COMPLETED)
        generate_output_latest = GenerateStoryStepOutput(
            story_id=uuid.uuid4(),
            title="Latest",
            word_count=800,
            act_count=4,
        )
        generate_output_old = GenerateStoryStepOutput(
            story_id=uuid.uuid4(),
            title="Old",
            word_count=700,
            act_count=3,
        )
        tts_output = TtsSynthesisStepOutput(
            run_id=uuid.uuid4(),
            chunk_count=2,
            total_duration_sec=7.5,
            gpu_pod_id="tts-pod",
        )
        image_output = ImageGenerationStepOutput(
            image_count=1,
            gpu_pod_id="image-pod",
        )
        completed_steps = [
            _step(
                source_id,
                StepName.GENERATE_STORY,
                status=StepStatus.COMPLETED,
                attempt_number=2,
                output_json=generate_output_latest,
            ),
            _step(
                source_id,
                StepName.GENERATE_STORY,
                status=StepStatus.COMPLETED,
                attempt_number=1,
                output_json=generate_output_old,
            ),
            _step(
                source_id,
                StepName.TTS_SYNTHESIS,
                status=StepStatus.COMPLETED,
                attempt_number=1,
                output_json=tts_output,
            ),
            _step(
                source_id,
                StepName.IMAGE_GENERATION,
                status=StepStatus.COMPLETED,
                attempt_number=1,
                output_json=image_output,
            ),
        ]

        source_scene = _scene(
            source_id,
            scene_index=0,
            scene_id=old_scene_id,
            image_status=ChunkStatus.COMPLETED,
        )
        source_scene.image_prompt = "foggy antenna"
        source_scene.image_negative_prompt = "bright daylight"
        source_scene.image_blob_id = image_blob_id
        source_scene.image_completed_at = datetime.now(timezone.utc)

        source_chunk = _chunk(
            source_id,
            chunk_index=0,
            chunk_text="The tower clicked.",
            tts_status=ChunkStatus.COMPLETED,
            scene_id=old_scene_id,
        )
        source_chunk.tts_audio_blob_id = wav_blob_id
        source_chunk.tts_mp3_blob_id = mp3_blob_id
        source_chunk.tts_duration_sec = 7.5
        source_chunk.tts_completed_at = datetime.now(timezone.utc)

        session = _mock_session()
        session.execute.side_effect = [
            _scalar_result(source_workflow),
            _scalars_result(completed_steps),
            _scalars_result([source_chunk]),
            _scalars_result([source_scene]),
        ]

        def assign_new_scene_id(scene: WorkflowScene) -> None:
            scene.id = new_scene_id

        session.refresh.side_effect = assign_new_scene_id

        with patch("app.services.workflow_service.uuid.uuid4", return_value=new_workflow_id):
            forked = await fork_workflow(session, source_id, StepName.STITCH_FINAL)

        assert forked.id == new_workflow_id
        assert forked.workflow_type == source_workflow.workflow_type
        assert forked.input_json is source_workflow.input_json
        assert forked.status == WorkflowStatus.RUNNING
        assert forked.started_at is not None

        added = [call.args[0] for call in session.add.call_args_list]
        seeded_steps = [item for item in added if isinstance(item, WorkflowStep)]
        copied_scenes = [item for item in added if isinstance(item, WorkflowScene)]
        copied_chunks = [item for item in added if isinstance(item, WorkflowChunk)]

        assert [step.step_name for step in seeded_steps] == [
            StepName.GENERATE_STORY,
            StepName.TTS_SYNTHESIS,
            StepName.IMAGE_GENERATION,
        ]
        assert [step.output_json for step in seeded_steps] == [
            generate_output_latest,
            tts_output,
            image_output,
        ]
        assert all(step.workflow_id == new_workflow_id for step in seeded_steps)
        assert all(step.status == StepStatus.COMPLETED for step in seeded_steps)
        assert all(step.attempt_number == 1 for step in seeded_steps)

        assert len(copied_scenes) == 1
        copied_scene = copied_scenes[0]
        assert copied_scene.workflow_id == new_workflow_id
        assert copied_scene.scene_index == source_scene.scene_index
        assert copied_scene.image_prompt == source_scene.image_prompt
        assert copied_scene.image_negative_prompt == source_scene.image_negative_prompt
        assert copied_scene.image_status == source_scene.image_status
        assert copied_scene.image_blob_id == image_blob_id

        assert len(copied_chunks) == 1
        copied_chunk = copied_chunks[0]
        assert copied_chunk.workflow_id == new_workflow_id
        assert copied_chunk.chunk_index == source_chunk.chunk_index
        assert copied_chunk.chunk_text == source_chunk.chunk_text
        assert copied_chunk.tts_status == source_chunk.tts_status
        assert copied_chunk.tts_audio_blob_id == wav_blob_id
        assert copied_chunk.tts_mp3_blob_id == mp3_blob_id
        assert copied_chunk.tts_duration_sec == source_chunk.tts_duration_sec
        assert copied_chunk.scene_id == new_scene_id
        assert session.flush.await_count == 4

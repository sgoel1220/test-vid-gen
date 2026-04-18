"""Tests for workflow step output persistence (bead s9j).

Verifies that:
- _as_step_output_schema returns the model for known concrete types.
- _as_step_output_schema returns None for unknown/generic Pydantic models.
- WorkflowService.complete_step stores the output in output_json.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from app.engine.runner import _as_step_output_schema
from app.models.json_schemas import (
    GenerateStoryStepOutput,
    ImageGenerationStepOutput,
    StitchFinalStepOutput,
    TtsSynthesisStepOutput,
)


# ---------------------------------------------------------------------------
# _as_step_output_schema
# ---------------------------------------------------------------------------


class TestAsStepOutputSchema:
    """Tests for the _as_step_output_schema helper."""

    def test_generate_story_step_output_is_recognized(self) -> None:
        output = GenerateStoryStepOutput(
            story_id=uuid.uuid4(),
            title="Test Story",
            word_count=500,
            act_count=3,
        )
        result = _as_step_output_schema(output)
        assert result is output

    def test_tts_synthesis_step_output_is_recognized(self) -> None:
        output = TtsSynthesisStepOutput(
            run_id=uuid.uuid4(),
            chunk_count=10,
            total_duration_sec=120.0,
            gpu_pod_id="pod-123",
        )
        result = _as_step_output_schema(output)
        assert result is output

    def test_image_generation_step_output_is_recognized(self) -> None:
        output = ImageGenerationStepOutput(
            image_count=5,
            gpu_pod_id="pod-456",
        )
        result = _as_step_output_schema(output)
        assert result is output

    def test_stitch_final_step_output_is_recognized(self) -> None:
        output = StitchFinalStepOutput(
            final_video_blob_id=uuid.uuid4(),
            total_duration_sec=360.0,
            file_size_bytes=1024 * 1024,
        )
        result = _as_step_output_schema(output)
        assert result is output

    def test_generic_pydantic_model_returns_none(self) -> None:
        class SomeOtherModel(BaseModel):
            value: int

        output = SomeOtherModel(value=42)
        result = _as_step_output_schema(output)
        assert result is None

    def test_empty_step_output_returns_none(self) -> None:
        from app.engine.models import EmptyStepOutput, SkippedStepOutput

        assert _as_step_output_schema(EmptyStepOutput()) is None
        assert _as_step_output_schema(SkippedStepOutput(reason="test")) is None


# ---------------------------------------------------------------------------
# WorkflowService.complete_step output persistence
# ---------------------------------------------------------------------------


class TestCompleteStepOutputPersistence:
    """Tests that WorkflowService.complete_step persists output_json."""

    @pytest.mark.asyncio
    async def test_complete_step_stores_output(self) -> None:
        """complete_step should set step.output_json when output is provided."""
        from datetime import datetime, timezone
        from app.models.enums import StepName, StepStatus
        from app.models.workflow import WorkflowStep
        from app.services.workflow_service import WorkflowService

        output = GenerateStoryStepOutput(
            story_id=uuid.uuid4(),
            title="Title",
            word_count=100,
            act_count=2,
        )

        workflow_id = uuid.uuid4()

        # Build a mock step in RUNNING state.
        mock_step = MagicMock(spec=WorkflowStep)
        mock_step.status = StepStatus.RUNNING
        mock_step.output_json = None

        scalar_result: MagicMock = MagicMock()
        scalar_result.scalar_one_or_none.return_value = mock_step

        session = AsyncMock()
        session.execute = AsyncMock(return_value=scalar_result)
        session.flush = AsyncMock()

        svc = WorkflowService(session)
        await svc.complete_step(workflow_id, StepName.GENERATE_STORY, output=output)

        assert mock_step.status == StepStatus.COMPLETED
        assert mock_step.output_json is output

    @pytest.mark.asyncio
    async def test_complete_step_without_output_leaves_output_json_unchanged(self) -> None:
        """complete_step with no output should not touch output_json."""
        from app.models.enums import StepName, StepStatus
        from app.models.workflow import WorkflowStep
        from app.services.workflow_service import WorkflowService

        workflow_id = uuid.uuid4()

        mock_step = MagicMock(spec=WorkflowStep)
        mock_step.status = StepStatus.RUNNING
        mock_step.output_json = None

        scalar_result: MagicMock = MagicMock()
        scalar_result.scalar_one_or_none.return_value = mock_step

        session = AsyncMock()
        session.execute = AsyncMock(return_value=scalar_result)
        session.flush = AsyncMock()

        svc = WorkflowService(session)
        await svc.complete_step(workflow_id, StepName.GENERATE_STORY)

        # output_json must not have been set (no attr assignment)
        assert mock_step.output_json is None

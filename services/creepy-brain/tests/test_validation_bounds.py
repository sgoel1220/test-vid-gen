"""Validation tests for bounded workflow and pipeline fields."""

import pytest
from pydantic import ValidationError

from app.models.json_schemas import StoryActOutline, WorkflowInputSchema
from app.pipeline.models import ActOutline, Beat, DimensionScore, TensionCurve
from app.schemas.workflow import CreateWorkflowRequest


def _beat() -> Beat:
    return Beat(description="finds door", purpose="setup", emotional_tone="uneasy")


class TestWorkflowBounds:
    def test_create_request_rejects_too_many_revisions(self) -> None:
        with pytest.raises(ValidationError):
            CreateWorkflowRequest(
                premise="A house at the edge of town",
                voice_name="old_man_low.wav",
                max_revisions=11,
            )

    def test_workflow_input_rejects_tiny_target_word_count(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowInputSchema(
                premise="A house at the edge of town",
                voice_name="old_man_low.wav",
                target_word_count=99,
            )


class TestPipelineBounds:
    def test_act_outline_rejects_invalid_tension_level(self) -> None:
        with pytest.raises(ValidationError):
            ActOutline(
                act_number=1,
                title="The Door",
                beats=[_beat()],
                act_hook="The handle moves by itself.",
                act_cliffhanger="The door opens.",
                subplots_active=[],
                tension_level=11,
            )

    def test_tension_curve_rejects_invalid_score(self) -> None:
        with pytest.raises(ValidationError):
            TensionCurve(act_1=1, act_2=3, act_3=5, act_4=8, act_5=11)

    def test_dimension_score_rejects_invalid_score(self) -> None:
        with pytest.raises(ValidationError):
            DimensionScore(
                subplot_completion=9.0,
                foreshadowing_payoff=9.0,
                character_consistency=9.0,
                pacing=9.0,
                ending_impact=9.0,
                overall_score=11.0,
            )

    def test_story_act_outline_accepts_small_word_count(self) -> None:
        """Derived word counts can be small (e.g., 200 words / 5 acts = 40)."""
        outline = StoryActOutline(
            act_number=1,
            title="The Door",
            summary="A door appears in a sealed room.",
            target_word_count=20,
            key_events=["The door opens."],
        )
        assert outline.target_word_count == 20

    def test_story_act_outline_rejects_zero_word_count(self) -> None:
        with pytest.raises(ValidationError):
            StoryActOutline(
                act_number=1,
                title="The Door",
                summary="A door appears in a sealed room.",
                target_word_count=0,
                key_events=["The door opens."],
            )

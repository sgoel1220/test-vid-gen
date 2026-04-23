"""Pydantic models for JSONB fields in SQLAlchemy models."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.models.step_params import BaseStepParams
from app.validation_limits import (
    DEFAULT_STORY_TARGET_WORD_COUNT,
    MAX_REVISIONS_MAX,
    MAX_REVISIONS_MIN,
    WORKFLOW_TARGET_WORD_COUNT_MAX,
    WORKFLOW_TARGET_WORD_COUNT_MIN,
)


# Per-step configurable parameter models
class StoryStepParams(BaseStepParams):
    """Configurable params for the story generation step.

    Story is a required step and cannot be disabled.
    """

    enabled: Literal[True] = True
    max_revisions: int = Field(default=3, ge=MAX_REVISIONS_MIN, le=MAX_REVISIONS_MAX)
    target_word_count: int = Field(
        default=DEFAULT_STORY_TARGET_WORD_COUNT,
        ge=WORKFLOW_TARGET_WORD_COUNT_MIN,
        le=WORKFLOW_TARGET_WORD_COUNT_MAX,
    )


class TtsStepParams(BaseStepParams):
    """Configurable params for the TTS synthesis step.

    TTS is a required step and cannot be disabled.
    """

    enabled: Literal[True] = True


class ImageStepParams(BaseStepParams):
    """Configurable params for the image generation step."""

    enabled: bool = Field(default=False, description="Whether to generate scene images via GPU pod")


class StitchStepParams(BaseStepParams):
    """Configurable params for the stitch/video step."""

    enabled: bool = Field(default=False, description="Whether to stitch final video")


# Workflow Input/Output Schemas
class WorkflowInputSchema(BaseModel):
    """Input data for a content pipeline workflow."""

    premise: str = Field(..., description="Story premise or prompt")
    voice_name: str = Field(..., description="Voice to use for TTS")
    manual_story_text: str | None = Field(
        default=None,
        description=(
            "If set, skip LLM story generation entirely and use this text as the completed story. "
            "Useful for manually authored or externally generated stories."
        ),
    )

    # Per-step params (fully typed, each with its own model)
    story_params: StoryStepParams = Field(default_factory=StoryStepParams)
    tts_params: TtsStepParams = Field(default_factory=TtsStepParams)
    image_params: ImageStepParams = Field(default_factory=ImageStepParams)
    stitch_params: StitchStepParams = Field(default_factory=StitchStepParams)

    # Deprecated — kept for backwards compat with existing DB rows
    generate_images: bool = Field(
        default=False, description="Deprecated: use image_params.enabled instead"
    )
    stitch_video: bool = Field(
        default=False, description="Deprecated: use stitch_params.enabled instead"
    )
    max_revisions: int = Field(
        default=3,
        ge=MAX_REVISIONS_MIN,
        le=MAX_REVISIONS_MAX,
        description="Deprecated: use story_params.max_revisions instead",
    )
    target_word_count: int = Field(
        default=DEFAULT_STORY_TARGET_WORD_COUNT,
        ge=WORKFLOW_TARGET_WORD_COUNT_MIN,
        le=WORKFLOW_TARGET_WORD_COUNT_MAX,
        description="Deprecated: use story_params.target_word_count instead",
    )

    @model_validator(mode="after")
    def _backfill_from_legacy(self) -> "WorkflowInputSchema":
        """Migrate legacy flat fields into typed step params for old DB rows."""
        if self.generate_images and not self.image_params.enabled:
            self.image_params.enabled = True
        if self.stitch_video and not self.stitch_params.enabled:
            self.stitch_params.enabled = True
        if self.target_word_count != DEFAULT_STORY_TARGET_WORD_COUNT:
            if self.story_params.target_word_count == DEFAULT_STORY_TARGET_WORD_COUNT:
                self.story_params.target_word_count = self.target_word_count
        if self.max_revisions != 3:
            if self.story_params.max_revisions == 3:
                self.story_params.max_revisions = self.max_revisions
        return self


class WorkflowResultSchema(BaseModel):
    """Result data from a completed workflow."""

    story_id: uuid.UUID | None = Field(None, description="Generated story ID")
    run_id: uuid.UUID | None = Field(None, description="TTS run ID")
    final_audio_blob_id: uuid.UUID | None = Field(None, description="Final audio blob ID")
    final_video_blob_id: uuid.UUID | None = Field(None, description="Final video blob ID")
    waveform_video_blob_id: uuid.UUID | None = Field(None, description="Waveform overlay video blob ID")
    total_duration_sec: float | None = Field(None, description="Total audio duration")
    chunk_count: int | None = Field(None, description="Number of chunks processed")
    gpu_pod_id: str | None = Field(None, description="GPU pod used")
    total_cost_cents: int | None = Field(None, description="Total cost in cents")


# Step Input Schemas (with discriminator)
class GenerateStoryStepInput(BaseModel):
    """Input for generate_story step."""

    step_type: Literal["generate_story"] = "generate_story"
    premise: str
    max_revisions: int = Field(default=3, ge=MAX_REVISIONS_MIN, le=MAX_REVISIONS_MAX)
    target_word_count: int = Field(
        default=200,
        ge=WORKFLOW_TARGET_WORD_COUNT_MIN,
        le=WORKFLOW_TARGET_WORD_COUNT_MAX,
    )


class TtsSynthesisStepInput(BaseModel):
    """Input for tts_synthesis step."""

    step_type: Literal["tts_synthesis"] = "tts_synthesis"
    story_id: uuid.UUID
    voice_name: str
    full_text: str


class ImageGenerationStepInput(BaseModel):
    """Input for image_generation step."""

    step_type: Literal["image_generation"] = "image_generation"
    workflow_id: uuid.UUID
    chunk_count: int
    gpu_pod_id: str


class StitchFinalStepInput(BaseModel):
    """Input for stitch_final step."""

    step_type: Literal["stitch_final"] = "stitch_final"
    workflow_id: uuid.UUID
    chunk_count: int


# Discriminated union for step inputs
StepInputSchema = Annotated[
    GenerateStoryStepInput
    | TtsSynthesisStepInput
    | ImageGenerationStepInput
    | StitchFinalStepInput,
    Field(discriminator="step_type"),
]


# Step Output Schemas (with discriminator)
class GenerateStoryStepOutput(BaseModel):
    """Output from generate_story step."""

    step_type: Literal["generate_story"] = "generate_story"
    story_id: uuid.UUID
    title: str
    word_count: int
    act_count: int


class TtsSynthesisStepOutput(BaseModel):
    """Output from tts_synthesis step."""

    step_type: Literal["tts_synthesis"] = "tts_synthesis"
    run_id: uuid.UUID
    chunk_count: int
    total_duration_sec: float
    gpu_pod_id: str


class ImageGenerationStepOutput(BaseModel):
    """Output from image_generation step."""

    step_type: Literal["image_generation"] = "image_generation"
    image_count: int
    gpu_pod_id: str


class StitchFinalStepOutput(BaseModel):
    """Output from stitch_final step."""

    step_type: Literal["stitch_final"] = "stitch_final"
    final_video_blob_id: uuid.UUID
    total_duration_sec: float
    file_size_bytes: int


class WaveformOverlayStepOutput(BaseModel):
    """Output from waveform_overlay step."""

    step_type: Literal["waveform_overlay"] = "waveform_overlay"
    waveform_video_blob_id: uuid.UUID
    file_size_bytes: int


# Discriminated union for step outputs
StepOutputSchema = Annotated[
    GenerateStoryStepOutput
    | TtsSynthesisStepOutput
    | ImageGenerationStepOutput
    | StitchFinalStepOutput
    | WaveformOverlayStepOutput,
    Field(discriminator="step_type"),
]


# Story Outline Schema
class StoryActOutline(BaseModel):
    """Outline for a single act in a story."""

    act_number: int
    title: str
    summary: str
    target_word_count: int = Field(ge=1)
    key_events: list[str]


class StoryOutlineSchema(BaseModel):
    """Structured outline for a story."""

    title: str
    total_acts: int
    total_target_words: int = Field(
        ge=WORKFLOW_TARGET_WORD_COUNT_MIN,
        le=WORKFLOW_TARGET_WORD_COUNT_MAX,
    )
    acts: list[StoryActOutline]
    themes: list[str]
    setting: str
    tone: str

"""Pydantic schemas for JSONB fields in SQLAlchemy models."""

import uuid
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self


# Workflow Input/Output Schemas
class WorkflowInputSchema(BaseModel):
    """Input data for a content pipeline workflow."""

    premise: str = Field(..., description="Story premise or prompt")
    voice_name: str = Field(..., description="Voice to use for TTS")
    generate_images: bool = Field(default=False, description="Whether to generate images (not yet implemented, bead 83y)")
    stitch_video: bool = Field(default=False, description="Whether to stitch final video (not yet implemented, bead ea6)")
    max_revisions: int = Field(default=3, description="Max story revision attempts")
    target_word_count: int = Field(default=5000, description="Target word count for story")

    @model_validator(mode="after")
    def reject_unimplemented_features(self) -> Self:
        """Fail fast before expensive story/TTS work if unimplemented features are requested."""
        if self.generate_images:
            raise ValueError(
                "generate_images=True is not yet implemented (tracked in bead 83y). "
                "Set generate_images=False to run story+TTS only."
            )
        if self.stitch_video:
            raise ValueError(
                "stitch_video=True is not yet implemented (tracked in bead ea6). "
                "Set stitch_video=False to run story+TTS only."
            )
        return self


class WorkflowResultSchema(BaseModel):
    """Result data from a completed workflow."""

    story_id: Optional[uuid.UUID] = Field(None, description="Generated story ID")
    run_id: Optional[uuid.UUID] = Field(None, description="TTS run ID")
    final_audio_blob_id: Optional[uuid.UUID] = Field(None, description="Final audio blob ID")
    final_video_blob_id: Optional[uuid.UUID] = Field(None, description="Final video blob ID")
    total_duration_sec: Optional[float] = Field(None, description="Total audio duration")
    chunk_count: Optional[int] = Field(None, description="Number of chunks processed")
    gpu_pod_id: Optional[str] = Field(None, description="GPU pod used")
    total_cost_cents: Optional[int] = Field(None, description="Total cost in cents")


# Step Input Schemas (with discriminator)
class GenerateStoryStepInput(BaseModel):
    """Input for generate_story step."""

    step_type: Literal["generate_story"] = "generate_story"
    premise: str
    max_revisions: int = 3
    target_word_count: int = 5000


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
    Union[
        GenerateStoryStepInput,
        TtsSynthesisStepInput,
        ImageGenerationStepInput,
        StitchFinalStepInput,
    ],
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


# Discriminated union for step outputs
StepOutputSchema = Annotated[
    Union[
        GenerateStoryStepOutput,
        TtsSynthesisStepOutput,
        ImageGenerationStepOutput,
        StitchFinalStepOutput,
    ],
    Field(discriminator="step_type"),
]


# Story Outline Schema
class StoryActOutline(BaseModel):
    """Outline for a single act in a story."""

    act_number: int
    title: str
    summary: str
    target_word_count: int
    key_events: list[str]


class StoryOutlineSchema(BaseModel):
    """Structured outline for a story."""

    title: str
    total_acts: int
    total_target_words: int
    acts: list[StoryActOutline]
    themes: list[str]
    setting: str
    tone: str

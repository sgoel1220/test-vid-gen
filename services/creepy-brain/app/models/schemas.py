"""Pydantic schemas for JSONB fields in SQLAlchemy models."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# Workflow Input/Output Schemas
class WorkflowInputSchema(BaseModel):
    """Input data for a content pipeline workflow."""

    premise: str = Field(..., description="Story premise or prompt")
    voice_name: str = Field(..., description="Voice to use for TTS")
    generate_images: bool = Field(default=False, description="Whether to generate scene images via GPU pod")
    stitch_video: bool = Field(default=False, description="Whether to stitch final video (not yet implemented, bead ea6)")
    max_revisions: int = Field(default=3, description="Max story revision attempts")
    target_word_count: int = Field(default=5000, description="Target word count for story")



class WorkflowResultSchema(BaseModel):
    """Result data from a completed workflow."""

    story_id: uuid.UUID | None = Field(None, description="Generated story ID")
    run_id: uuid.UUID | None = Field(None, description="TTS run ID")
    final_audio_blob_id: uuid.UUID | None = Field(None, description="Final audio blob ID")
    final_video_blob_id: uuid.UUID | None = Field(None, description="Final video blob ID")
    total_duration_sec: float | None = Field(None, description="Total audio duration")
    chunk_count: int | None = Field(None, description="Number of chunks processed")
    gpu_pod_id: str | None = Field(None, description="GPU pod used")
    total_cost_cents: int | None = Field(None, description="Total cost in cents")


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


# Discriminated union for step outputs
StepOutputSchema = Annotated[
    GenerateStoryStepOutput
    | TtsSynthesisStepOutput
    | ImageGenerationStepOutput
    | StitchFinalStepOutput,
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

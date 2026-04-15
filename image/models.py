"""Pydantic v2 request/response models for image generation."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from enums import ImageStyle, JobStatus


class ScenePrompt(BaseModel):
    scene_index: int = Field(..., ge=0, description="Zero-based scene index.")
    text_segment: str = Field(..., description="Source text segment this scene represents.")
    prompt: str = Field(..., min_length=1, description="Positive prompt for SDXL.")
    negative_prompt: str = Field(
        "low quality, blurry, text, watermark, logo, bright, cheerful, cartoon",
        description="Negative prompt for SDXL.",
    )


class ImageGenRequest(BaseModel):
    story_text: str = Field(..., min_length=1, description="Full story text to generate images for.")
    num_scenes: int = Field(4, ge=1, le=20, description="Number of visual scenes to extract.")
    style: ImageStyle = Field(ImageStyle.DARK_ATMOSPHERIC, description="Visual style preset.")
    width: int = Field(1024, ge=512, le=2048, description="Output image width in pixels.")
    height: int = Field(1024, ge=512, le=2048, description="Output image height in pixels.")
    steps: Optional[int] = Field(None, ge=1, le=100, description="Override inference steps.")
    guidance_scale: Optional[float] = Field(None, ge=1.0, le=30.0, description="Override CFG scale.")
    seed: Optional[int] = Field(None, ge=0, description="Random seed for reproducibility.")
    manual_prompts: Optional[List[ScenePrompt]] = Field(
        None, description="Skip LLM extraction and use these prompts directly.",
    )
    unload_tts_for_vram: bool = Field(
        False, description="Unload TTS model before image gen to free VRAM, reload after.",
    )
    run_label: Optional[str] = Field(None, description="Optional label for the output directory.")


class PromptPreviewRequest(BaseModel):
    story_text: str = Field(..., min_length=1, description="Story text to extract prompts from.")
    num_scenes: int = Field(4, ge=1, le=20, description="Number of visual scenes to extract.")
    style: ImageStyle = Field(ImageStyle.DARK_ATMOSPHERIC, description="Visual style preset.")


class SavedImageArtifact(BaseModel):
    filename: str
    relative_path: str
    url: str
    width: int
    height: int
    prompt_used: str
    negative_prompt_used: str
    seed_used: int


class ImageGenResponse(BaseModel):
    run_id: str
    output_dir: str
    scenes: List[ScenePrompt]
    images: List[SavedImageArtifact]
    manifest_relative_path: str
    manifest_url: str
    warnings: List[str] = Field(default_factory=list)


class ImageJobCreatedResponse(BaseModel):
    job_id: str
    status_url: str


class ImageJobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    progress_completed: int = 0
    progress_total: int = 0
    result: Optional[ImageGenResponse] = None
    error: Optional[str] = None

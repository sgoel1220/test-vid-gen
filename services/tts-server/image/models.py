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
        (
            "people, humans, faces, characters, person, man, woman, child, "
            "figure, portrait, body, hands, eyes, "
            "low quality, blurry, text, watermark, logo, signature, "
            "bright, cheerful, cartoon, anime, 3d render, cgi, "
            "artificial, digital art, computer generated, pixelated"
        ),
        description="Negative prompt for SDXL (enforces background-only, painting style).",
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


class ManualImageGenRequest(BaseModel):
    """Simple manual prompt → single image generation."""
    prompt: str = Field(..., min_length=1, description="Text prompt for image generation.")
    negative_prompt: str = Field(
        "",
        description="Negative prompt (things to avoid in the image).",
    )
    width: int = Field(1024, ge=512, le=2048, description="Output image width in pixels.")
    height: int = Field(1024, ge=512, le=2048, description="Output image height in pixels.")
    steps: Optional[int] = Field(None, ge=1, le=100, description="Override inference steps.")
    guidance_scale: Optional[float] = Field(None, ge=0.0, le=30.0, description="Override CFG scale.")
    seed: Optional[int] = Field(None, ge=0, description="Random seed for reproducibility.")
    run_label: Optional[str] = Field(None, description="Optional label for the output directory.")


class ManualImageGenResponse(BaseModel):
    """Response for single manual image generation."""
    run_id: str
    output_dir: str
    image: SavedImageArtifact
    manifest_url: str


class ChunkBasedImageGenRequest(BaseModel):
    """Request for chunk-based image generation (post-TTS workflow)."""
    chunks: List[str] = Field(..., min_length=1, description="TTS text chunks to group and generate images for.")
    chunks_per_group: int = Field(
        5, ge=1, le=10, description="Target number of chunks to group together per image."
    )
    style: ImageStyle = Field(ImageStyle.DARK_ATMOSPHERIC, description="Visual style preset.")
    width: int = Field(1024, ge=512, le=2048, description="Output image width in pixels.")
    height: int = Field(1024, ge=512, le=2048, description="Output image height in pixels.")
    steps: Optional[int] = Field(None, ge=1, le=100, description="Override inference steps.")
    guidance_scale: Optional[float] = Field(None, ge=1.0, le=30.0, description="Override CFG scale.")
    seed: Optional[int] = Field(None, ge=0, description="Random seed for reproducibility.")
    run_label: Optional[str] = Field(None, description="Optional label for the output directory.")


class ChunkGroupPreviewResponse(BaseModel):
    """Preview of chunk grouping without generating images."""
    chunk_groups: List[dict] = Field(..., description="List of chunk groups with background descriptions.")
    total_groups: int = Field(..., description="Total number of groups created.")
    chunks_processed: int = Field(..., description="Total number of chunks processed.")

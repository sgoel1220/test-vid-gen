"""Scene-level image prompt generation via LLM for SDXL backgrounds."""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.llm.client import generate_structured

log = logging.getLogger(__name__)

# Maximum retries for validation failures
_MAX_VALIDATION_RETRIES = 2

# Maximum word count for prompts (SDXL performs best with concise prompts)
_MAX_PROMPT_WORDS = 200

# Terms that should NOT appear in positive prompts (we want backgrounds only)
_FORBIDDEN_PROMPT_TERMS = frozenset(
    [
        "person",
        "people",
        "human",
        "man",
        "woman",
        "child",
        "face",
        "portrait",
        "character",
        "figure",
        "body",
    ]
)


class ImagePromptResult(BaseModel):
    """Result of generating an SDXL image prompt for a scene."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        min_length=10,
        description="Positive prompt for SDXL describing the visual setting/atmosphere",
    )
    negative_prompt: str = Field(
        min_length=10,
        description="Negative prompt to avoid unwanted elements",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        """Validate positive prompt is within word limit and has no forbidden terms."""
        words = v.split()
        if len(words) > _MAX_PROMPT_WORDS:
            raise ValueError(
                f"Prompt exceeds {_MAX_PROMPT_WORDS} words ({len(words)} words)"
            )

        # Check for forbidden terms (case-insensitive)
        lower_prompt = v.lower()
        found_forbidden = [
            term for term in _FORBIDDEN_PROMPT_TERMS if re.search(rf"\b{term}\b", lower_prompt)
        ]
        if found_forbidden:
            raise ValueError(
                f"Prompt contains forbidden terms (should be background only): {found_forbidden}"
            )

        return v


# Base negative prompt terms that should always be included
_BASE_NEGATIVE_TERMS = (
    "text, words, letters, watermark, signature, blurry, low quality, "
    "pixelated, compression artifacts, jpeg artifacts, out of focus"
)

_IMAGE_PROMPT_SYSTEM = f"""\
You are creating a background image prompt for a horror narration video.

Your task is to generate an SDXL-optimized image prompt that captures the \
visual setting and atmosphere of a scene from a creepy pasta story.

Guidelines for the POSITIVE prompt:
- Focus on ENVIRONMENT, LIGHTING, and MOOD — not character actions
- Describe the physical setting: room, building, landscape, weather
- Emphasize atmospheric elements: shadows, fog, dim lighting, decay
- Use cinematic language: "cinematic lighting", "moody atmosphere", "film grain"
- Include specific visual details: textures, colors, time of day
- Keep it under 150 words for optimal SDXL performance
- NO characters, people, or human figures
- NO text, signs, or written words in the scene

Guidelines for the NEGATIVE prompt:
- ALWAYS include: {_BASE_NEGATIVE_TERMS}
- Add scene-specific unwanted elements (e.g., "bright daylight" for a night scene)
- Keep it concise but comprehensive

Output format: JSON object with "prompt" and "negative_prompt" keys."""

_IMAGE_PROMPT_USER = """\
Scene text from a horror story:
---
{scene_text}
---

Generate an SDXL image prompt for this scene's background."""


class ImagePromptValidationError(Exception):
    """Raised when image prompt generation fails validation after retries."""

    pass


async def generate_scene_image_prompt(scene_text: str) -> ImagePromptResult:
    """Generate a cinematic SDXL image prompt for a horror scene.

    Args:
        scene_text: Combined text of all chunks in the scene.

    Returns:
        ImagePromptResult with positive and negative prompts for SDXL.

    Raises:
        ImagePromptValidationError: If LLM response fails validation after retries.
        Exception: On LLM API failures after retries.
    """
    last_error: ValidationError | None = None

    for attempt in range(_MAX_VALIDATION_RETRIES + 1):
        try:
            result = await generate_structured(
                system=_IMAGE_PROMPT_SYSTEM,
                user=_IMAGE_PROMPT_USER.format(scene_text=scene_text),
                response_model=ImagePromptResult,
            )

            # Ensure base negative terms are always included
            if _BASE_NEGATIVE_TERMS not in result.negative_prompt:
                result = ImagePromptResult(
                    prompt=result.prompt,
                    negative_prompt=f"{_BASE_NEGATIVE_TERMS}, {result.negative_prompt}",
                )

            log.info("Generated image prompt for scene (attempt %d)", attempt + 1)
            return result

        except ValidationError as exc:
            last_error = exc
            log.warning(
                "Image prompt validation failed (attempt %d/%d): %s",
                attempt + 1,
                _MAX_VALIDATION_RETRIES + 1,
                str(exc)[:200],
            )
            if attempt < _MAX_VALIDATION_RETRIES:
                continue
            break

    raise ImagePromptValidationError(
        f"Failed to generate valid image prompt after {_MAX_VALIDATION_RETRIES + 1} attempts: {last_error}"
    )

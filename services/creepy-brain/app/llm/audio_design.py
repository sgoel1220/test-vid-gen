"""LLM-based prompt generation for music moods and SFX cue lists."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.llm.client import generate_structured

log = logging.getLogger(__name__)

_MAX_VALIDATION_RETRIES = 2

# Keywords that must appear in a valid ACE-Step music prompt
_REQUIRED_MUSIC_KEYWORD_GROUPS: list[frozenset[str]] = [
    # mood/genre
    frozenset(
        [
            "ambient",
            "dark",
            "eerie",
            "tense",
            "horror",
            "suspense",
            "haunting",
            "ominous",
            "melancholic",
            "atmospheric",
            "dramatic",
            "foreboding",
        ]
    ),
    # tempo indicator
    frozenset(["bpm", "tempo", "slow", "fast", "moderate", "pulse"]),
    # instrumentation — classical acoustic only, 1-2 instruments preferred
    # Piano is primary; cello/violin/strings as solo secondary option
    frozenset(
        [
            "piano",
            "strings",
            "violin",
            "cello",
            "flute",
            "harp",
        ]
    ),
]

# Vocal/choir terms are forbidden in music prompts — narration must remain primary
_FORBIDDEN_MUSIC_TERMS: frozenset[str] = frozenset(
    ["choir", "choral", "vocals", "vocal", "lyrics", "singing", "voice", "voices", "acapella"]
)

_VALID_POSITIONS: frozenset[str] = frozenset(["beginning", "middle", "end"])


# ---------------------------------------------------------------------------
# Music mood models
# ---------------------------------------------------------------------------


class MusicMoodResult(BaseModel):
    """Result of generating an ACE-Step music mood prompt for a scene."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(
        min_length=10,
        description=(
            "ACE-Step prompt describing the music mood, e.g. "
            "'dark ambient, solo piano, 55 BPM, haunting' or "
            "'eerie atmospheric, piano and cello, slow pulse, melancholic'"
        ),
    )
    intensity: int = Field(
        ge=1,
        le=10,
        description="Emotional intensity of the scene on a 1-10 scale",
    )

    @field_validator("prompt")
    @classmethod
    def validate_prompt_keywords(cls, v: str) -> str:
        """Ensure prompt covers mood, tempo, and instrumentation; reject vocals."""
        lower = v.lower()
        # Must contain at least one keyword from each required group
        for group in _REQUIRED_MUSIC_KEYWORD_GROUPS:
            if not any(kw in lower for kw in group):
                raise ValueError(
                    f"Music prompt must include at least one of: {sorted(group)}"
                )
        # Must not contain any vocal/choir terms — narration is the primary voice
        found_vocal = [kw for kw in _FORBIDDEN_MUSIC_TERMS if kw in lower]
        if found_vocal:
            raise ValueError(
                f"Music prompt must be instrumental only; remove vocal terms: {found_vocal}"
            )
        return v


# ---------------------------------------------------------------------------
# SFX cue models
# ---------------------------------------------------------------------------


class SfxCue(BaseModel):
    """A single sound-effect cue for a scene."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        min_length=5,
        description="Natural-language description of the sound, e.g. 'heavy wooden door creaking open'",
    )
    position: Literal["beginning", "middle", "end"] = Field(
        description="Where in the scene this cue should play",
    )
    duration_sec: float = Field(
        ge=1.0,
        le=10.0,
        description="Desired duration of the sound effect in seconds (1–10)",
    )


class SfxCueListResult(BaseModel):
    """List of SFX cues generated for a scene."""

    model_config = ConfigDict(extra="forbid")

    cues: list[SfxCue] = Field(
        max_length=5,
        description="Ordered list of up to 5 sound-effect cues for the scene",
    )


# ---------------------------------------------------------------------------
# System / user prompt templates
# ---------------------------------------------------------------------------

_MUSIC_MOOD_SYSTEM = """\
You are a music director for a horror narration video series.

Your task is to create an ACE-Step music generation prompt based SOLELY on the \
emotional mood and atmosphere of a scene — NOT on its specific plot, characters, or events.

Focus exclusively on:
- The dominant emotion (e.g. dread, grief, unease, panic, melancholy, suspense)
- The tension level and pacing feel
- The general atmospheric quality (e.g. claustrophobic, desolate, creeping, surreal)

Ignore story-specific details. Two scenes with the same mood should produce similar prompts.

ACE-Step prompt format — combine these elements with commas:
1. GENRE/MOOD: e.g. "dark ambient", "horror soundtrack", "eerie atmospheric"
2. INSTRUMENTATION: use piano as primary; optionally add ONE classical string instrument (cello, violin, strings, flute, or harp). Never use more than 2 instruments total. No synth, drums, bass, guitar, brass, organ, or electronic instruments.
3. TEMPO: always include an explicit BPM or qualifier, e.g. "55 BPM", "slow pulse"
4. ADDITIONAL MOOD TAGS: e.g. "haunting", "tense", "foreboding", "melancholic"

Guidelines:
- Keep prompts concise: 8–15 comma-separated descriptors
- Match the intensity to the emotional stakes (1=calm ambience, 10=peak terror)
- ALWAYS use piano as the primary or sole instrument — it produces the highest quality output
- Prefer solo piano ("solo piano", "sparse piano") for most scenes — add a second instrument only when the mood genuinely calls for it (e.g. cello for deep grief, violin for tension)
- Sparse, minimal arrangements sit cleanly under narration without masking the voice
- STRICTLY instrumental — no vocals, choir, choral, or singing of any kind

Output format: JSON object with "prompt" (string) and "intensity" (integer 1-10) keys."""

_MUSIC_MOOD_USER = """\
Scene text:
---
{scene_text}
---

Identify the dominant emotional mood of this scene (ignore plot specifics), \
then generate an ACE-Step music prompt and intensity score that fits that mood."""

_SFX_SYSTEM = """\
You are a sound designer for a horror narration video series.

Your task is to select up to 5 diegetic sound effects (SFX) that enhance \
immersion for a scene from a creepy pasta story.

Guidelines for SFX descriptions:
- Be specific and vivid: "heavy oak door creaking on rusted hinges" beats "door sound"
- Match position to narrative pacing:
  * beginning — establishes the scene environment
  * middle — reacts to plot action
  * end — creates transition tension or resolution
- Duration should reflect realism (1–10 seconds)
- Fewer, well-chosen cues beat many weak ones — use 1–5 cues only
- Avoid music descriptions — focus on environmental and foley sounds

Output format: JSON object with a "cues" array. Each cue has:
  "description" (string), "position" ("beginning"|"middle"|"end"), "duration_sec" (float)."""

_SFX_USER = """\
Scene text from a horror story:
---
{scene_text}
---

Generate a list of SFX cues for this scene."""


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class AudioDesignValidationError(Exception):
    """Raised when audio design generation fails validation after retries."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def generate_music_mood(scene_text: str) -> MusicMoodResult:
    """Generate an ACE-Step music mood prompt for a horror scene.

    Args:
        scene_text: Combined text of all chunks in the scene.

    Returns:
        MusicMoodResult with prompt and intensity score.

    Raises:
        AudioDesignValidationError: If LLM response fails validation after retries.
        Exception: On LLM API failures after retries.
    """
    last_error: ValidationError | None = None

    for attempt in range(_MAX_VALIDATION_RETRIES + 1):
        try:
            result = await generate_structured(
                system=_MUSIC_MOOD_SYSTEM,
                user=_MUSIC_MOOD_USER.format(scene_text=scene_text),
                response_model=MusicMoodResult,
            )
            log.info(
                "Generated music mood prompt (attempt %d): intensity=%d prompt=%.80s",
                attempt + 1,
                result.intensity,
                result.prompt,
            )
            return result

        except ValidationError as exc:
            last_error = exc
            log.warning(
                "Music mood validation failed (attempt %d/%d): %s",
                attempt + 1,
                _MAX_VALIDATION_RETRIES + 1,
                str(exc)[:200],
            )
            if attempt < _MAX_VALIDATION_RETRIES:
                continue
            break

    raise AudioDesignValidationError(
        f"Failed to generate valid music mood after {_MAX_VALIDATION_RETRIES + 1} attempts: "
        f"{last_error}"
    )


async def generate_sfx_cues(scene_text: str) -> SfxCueListResult:
    """Generate a list of SFX cues for a horror scene.

    Args:
        scene_text: Combined text of all chunks in the scene.

    Returns:
        SfxCueListResult with up to 5 cues.

    Raises:
        AudioDesignValidationError: If LLM response fails validation after retries.
        Exception: On LLM API failures after retries.
    """
    last_error: ValidationError | None = None

    for attempt in range(_MAX_VALIDATION_RETRIES + 1):
        try:
            result = await generate_structured(
                system=_SFX_SYSTEM,
                user=_SFX_USER.format(scene_text=scene_text),
                response_model=SfxCueListResult,
            )
            log.info(
                "Generated %d SFX cues (attempt %d)",
                len(result.cues),
                attempt + 1,
            )
            return result

        except ValidationError as exc:
            last_error = exc
            log.warning(
                "SFX cue validation failed (attempt %d/%d): %s",
                attempt + 1,
                _MAX_VALIDATION_RETRIES + 1,
                str(exc)[:200],
            )
            if attempt < _MAX_VALIDATION_RETRIES:
                continue
            break

    raise AudioDesignValidationError(
        f"Failed to generate valid SFX cues after {_MAX_VALIDATION_RETRIES + 1} attempts: "
        f"{last_error}"
    )

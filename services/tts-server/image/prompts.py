"""Scene prompt extraction via local Qwen model — reuses text/normalization.py singleton."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from enums import ImageStyle
from image.models import ScenePrompt

if TYPE_CHECKING:
    from image.chunk_grouper import ChunkGroup

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "scene_v1"
_DEFAULT_CACHE_DIR = Path("outputs") / "scene_prompt_cache"

_SYSTEM_PROMPT = (
    "You are a visual scene extraction assistant for horror story narration videos. "
    "Given a horror/creepy pasta story text, extract exactly {num_scenes} key visual scenes "
    "that would make compelling, atmospheric background images.\n\n"
    "CRITICAL RULES:\n"
    "- Describe ONLY backgrounds/environments — no humans, no faces, no characters, no people\n"
    "- Focus on: landscapes, rooms, buildings, weather, lighting, nature, atmosphere\n"
    "- Imagine hand-painted, realistic artwork — NOT computer-generated imagery\n"
    "- Think traditional painting: brushstrokes, texture, depth, atmospheric perspective\n\n"
    "For each scene, output a JSON array of objects with these fields:\n"
    '- "scene_index": integer starting at 0\n'
    '- "text_segment": the short excerpt (1-2 sentences) from the story this scene represents\n'
    '- "prompt": a detailed prompt describing the BACKGROUND scene ONLY '
    "(composition, lighting, mood, colors, setting, environment; "
    "30-50 words max; NO humans, NO characters, NO complex objects)\n"
    '- "negative_prompt": things to avoid (keep short, comma-separated)\n\n'
    "Output ONLY valid JSON — no explanation, no markdown fences, no preamble."
)

# Painting-style base suffix for realistic, hand-painted look
_PAINTING_STYLE_BASE = (
    "oil painting style, traditional art, brushstrokes visible, "
    "painterly texture, canvas texture, realistic lighting, "
    "atmospheric perspective, depth of field, "
    "masterful composition, fine art quality, "
    "high detail, photorealistic render"
)

# Style-specific suffixes appended to every positive prompt (enhanced with painting quality)
_STYLE_SUFFIXES: dict[ImageStyle, str] = {
    ImageStyle.DARK_ATMOSPHERIC: (
        f"{_PAINTING_STYLE_BASE}, "
        "dark moody atmosphere, dramatic shadows, desaturated colors, "
        "volumetric fog, chiaroscuro lighting, muted palette"
    ),
    ImageStyle.COSMIC_HORROR: (
        f"{_PAINTING_STYLE_BASE}, "
        "lovecraftian cosmic void, impossible geometry, "
        "vast unknowable space, eldritch atmosphere, bioluminescence, "
        "dark teal and purple palette, otherworldly"
    ),
    ImageStyle.GOTHIC: (
        f"{_PAINTING_STYLE_BASE}, "
        "gothic architecture, candlelit interior, ornate decay, "
        "dark romanticism aesthetic, stained glass windows, moonlit, "
        "baroque details, dramatic lighting"
    ),
    ImageStyle.SURREAL_NIGHTMARE: (
        f"{_PAINTING_STYLE_BASE}, "
        "surreal dreamscape, distorted perspective, melting reality, "
        "Dali-esque composition, unsettling atmosphere, dreamlike quality, "
        "muted dark palette, psychological tension"
    ),
    ImageStyle.FOUND_FOOTAGE: (
        f"{_PAINTING_STYLE_BASE}, "
        "grainy aesthetic, atmospheric grain, night vision tint, "
        "security camera perspective, low-fi quality, eerie stillness, "
        "found footage mood, documentary realism"
    ),
    ImageStyle.PSYCHOLOGICAL: (
        f"{_PAINTING_STYLE_BASE}, "
        "psychological tension, uncanny atmosphere, eerie stillness, "
        "desaturated colors, isolation, liminal space aesthetic, "
        "minimalist composition, quiet horror"
    ),
    ImageStyle.FOLK_HORROR: (
        f"{_PAINTING_STYLE_BASE}, "
        "folk horror aesthetic, ancient rural landscape, decaying countryside, "
        "pagan symbolism, misty fields, twilight hour, "
        "wicker textures, earthy palette"
    ),
    ImageStyle.BODY_HORROR: (
        f"{_PAINTING_STYLE_BASE}, "
        "biomechanical environment, organic architecture, fleshy textures, "
        "clinical cold lighting, visceral atmosphere, "
        "unsettling transformation, body horror palette"
    ),
}


def _cache_key(text: str, num_scenes: int, style: ImageStyle, model_id: str) -> str:
    payload = f"{_PROMPT_VERSION}|{model_id}|{style.value}|{num_scenes}|{text}"
    return hashlib.sha256(payload.encode()).hexdigest()


_MAX_PROMPT_WORDS = 55  # CLIP caps at ~77 tokens; ~55 words stays safe


def _trim_prompt(prompt: str) -> str:
    """Trim prompt to stay within CLIP's 77-token limit."""
    words = prompt.split()
    if len(words) <= _MAX_PROMPT_WORDS:
        return prompt
    return " ".join(words[:_MAX_PROMPT_WORDS])


def _apply_style_suffix(scenes: List[ScenePrompt], style: ImageStyle) -> List[ScenePrompt]:
    suffix = _STYLE_SUFFIXES.get(style, _STYLE_SUFFIXES[ImageStyle.DARK_ATMOSPHERIC])
    styled: List[ScenePrompt] = []
    for scene in scenes:
        combined = _trim_prompt(f"{scene.prompt}, {suffix}")
        styled.append(
            ScenePrompt(
                scene_index=scene.scene_index,
                text_segment=scene.text_segment,
                prompt=combined,
                negative_prompt=scene.negative_prompt,
            )
        )
    return styled


def _parse_scenes_json(raw: str, num_scenes: int) -> List[ScenePrompt]:
    """Parse LLM output into ScenePrompt list. Raises ValueError on bad JSON."""
    # Strip markdown fences if the LLM wrapped them despite instructions.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n") if "\n" in cleaned else 3
        cleaned = cleaned[first_newline:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    items = json.loads(cleaned)
    if not isinstance(items, list):
        raise ValueError("Expected JSON array")

    scenes: List[ScenePrompt] = []
    for i, item in enumerate(items[:num_scenes]):
        scenes.append(
            ScenePrompt(
                scene_index=item.get("scene_index", i),
                text_segment=item.get("text_segment", ""),
                prompt=item.get("prompt", ""),
                negative_prompt=item.get(
                    "negative_prompt",
                    "people, humans, faces, characters, person, man, woman, child, "
                    "figure, portrait, body, hands, eyes, "
                    "low quality, blurry, text, watermark, logo, signature, "
                    "bright, cheerful, cartoon, anime, 3d render, cgi, "
                    "artificial, digital art, computer generated, pixelated",
                ),
            )
        )
    return scenes


def extract_scene_prompts(
    story_text: str,
    num_scenes: int = 4,
    style: ImageStyle = ImageStyle.DARK_ATMOSPHERIC,
    model_id: str = "Qwen/Qwen2.5-1.5B-Instruct",
    max_new_tokens: int = 4096,
    cache_dir: Optional[Path] = None,
) -> List[ScenePrompt]:
    """Extract visual scene prompts from story text using a local LLM.

    Returns a list of ScenePrompt with style suffixes applied.
    Falls back to a single generic prompt on any failure.
    """
    if not story_text or not story_text.strip():
        return []

    cache_dir = cache_dir or _DEFAULT_CACHE_DIR
    key = _cache_key(story_text, num_scenes, style, model_id)
    cached_path = cache_dir / f"{key}.json"

    # --- cache read ---
    if cached_path.exists():
        try:
            payload = json.loads(cached_path.read_text(encoding="utf-8"))
            raw_scenes = [ScenePrompt.model_validate(s) for s in payload["scenes"]]
            if raw_scenes:
                logger.debug("Scene prompt cache hit (%s…).", key[:12])
                return _apply_style_suffix(raw_scenes, style)
        except Exception as exc:
            logger.warning("Failed to read scene prompt cache %s: %s", cached_path, exc)

    # --- LLM inference (reuse text/normalization singleton) ---
    try:
        from text.normalization import _load_model as _load_qwen

        model, tokenizer = _load_qwen(model_id)
        if model is None or tokenizer is None:
            logger.warning("Qwen LLM unavailable — returning fallback scene prompts.")
            return _fallback_scenes(story_text, num_scenes, style)

        import torch as _torch

        system_content = _SYSTEM_PROMPT.format(num_scenes=num_scenes)
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": story_text},
        ]
        if hasattr(tokenizer, "apply_chat_template"):
            formatted = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            formatted = (
                f"<|system|>{system_content}</s><|user|>{story_text}</s><|assistant|>"
            )

        inputs = tokenizer(formatted, return_tensors="pt").to(model.device)
        input_len = inputs["input_ids"].shape[1]
        with _torch.no_grad():
            output_ids = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                pad_token_id=tokenizer.eos_token_id,
            )
        raw_output = tokenizer.decode(output_ids[0][input_len:], skip_special_tokens=True).strip()

        scenes = _parse_scenes_json(raw_output, num_scenes)
        if not scenes:
            logger.warning("LLM returned empty scene list — using fallback.")
            return _fallback_scenes(story_text, num_scenes, style)

        # --- cache write ---
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached_path.write_text(
                json.dumps(
                    {
                        "scenes": [s.model_dump() for s in scenes],
                        "model_id": model_id,
                        "prompt_version": _PROMPT_VERSION,
                        "num_scenes": num_scenes,
                        "style": style.value,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to write scene prompt cache: %s", exc)

        logger.info("Extracted %d scene prompts from story text.", len(scenes))
        return _apply_style_suffix(scenes, style)

    except Exception as exc:
        logger.warning("Scene prompt extraction failed (%s) — returning fallback.", exc, exc_info=True)
        return _fallback_scenes(story_text, num_scenes, style)


def _fallback_scenes(
    story_text: str, num_scenes: int, style: ImageStyle
) -> List[ScenePrompt]:
    """Generate minimal fallback prompts when LLM extraction fails."""
    snippet = story_text[:200].strip()
    scenes = [
        ScenePrompt(
            scene_index=i,
            text_segment=snippet if i == 0 else "",
            prompt=f"A dark and eerie background landscape from a horror story, atmospheric environment, scene {i + 1}",
            negative_prompt=(
                "people, humans, faces, characters, person, man, woman, child, "
                "figure, portrait, body, hands, eyes, "
                "low quality, blurry, text, watermark, logo, signature, "
                "bright, cheerful, cartoon, anime, 3d render, cgi, "
                "artificial, digital art, computer generated, pixelated"
            ),
        )
        for i in range(num_scenes)
    ]
    return _apply_style_suffix(scenes, style)


def chunk_groups_to_scene_prompts(
    chunk_groups: List["ChunkGroup"],
    style: ImageStyle = ImageStyle.DARK_ATMOSPHERIC,
) -> List[ScenePrompt]:
    """Convert ChunkGroup background descriptions into ScenePrompt for SDXL.

    Args:
        chunk_groups: List of chunk groups with background descriptions
        style: Visual style preset to apply

    Returns:
        List of ScenePrompt ready for image generation
    """
    scenes: List[ScenePrompt] = []

    for group in chunk_groups:
        # Use the LLM-generated background description as the base prompt
        base_prompt = group.background_description.strip()

        # Build a snippet from the first chunk in the group
        snippet_idx = group.chunk_indices[0] if group.chunk_indices else 0
        text_segment = f"Chunks {group.chunk_indices[0]}-{group.chunk_indices[-1]}" if group.chunk_indices else ""

        scenes.append(
            ScenePrompt(
                scene_index=group.group_index,
                text_segment=text_segment,
                prompt=base_prompt,
                negative_prompt=(
                    "people, humans, faces, characters, person, man, woman, child, "
                    "figure, portrait, body, hands, eyes, "
                    "low quality, blurry, text, watermark, logo, signature, "
                    "bright, cheerful, cartoon, anime, 3d render, cgi, "
                    "artificial, digital art, computer generated, pixelated"
                ),
            )
        )

    logger.info("Converted %d chunk groups to scene prompts.", len(scenes))
    return _apply_style_suffix(scenes, style)

"""LLM-based text normalization for TTS synthesis.

Normalizes story text to remove markdown, fix punctuation, and produce
clean prose suitable for TTS. Results are cached in-memory by text hash
to avoid redundant LLM calls across Hatchet step retries within the same
worker process.
"""

from __future__ import annotations

import hashlib
import logging

from app.llm.client import generate_text

log = logging.getLogger(__name__)

# In-process normalization cache: text_hash -> normalized_text
_cache: dict[str, str] = {}

_SYSTEM_PROMPT = """\
You are a text normalizer for text-to-speech (TTS) audio production.

Your task is to clean and normalize story text so it sounds natural when read aloud by a TTS engine.

Rules:
- Remove ALL markdown formatting: no asterisks, underscores, headers (#), bullet points, or other markup
- Remove chapter labels, act labels, and section headers
- Fix punctuation for natural spoken flow (em-dashes → commas or pauses, ellipses → natural pauses)
- Spell out abbreviations that would sound odd when read aloud (e.g. "Dr." → "Doctor")
- Keep all story content intact — do NOT summarize, cut, or rewrite the prose
- Output ONLY the cleaned prose text, nothing else"""

_USER_TEMPLATE = """\
Normalize the following story text for TTS narration:

{text}"""


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def normalize_text(text: str) -> str:
    """Normalize *text* for TTS synthesis via a single LLM API call.

    Results are cached in-process by SHA-256 hash of the input, so Hatchet
    step retries within the same worker process will not re-call the LLM.

    Args:
        text: Raw story text (may contain markdown, headers, etc.).

    Returns:
        Cleaned prose text suitable for TTS synthesis.
    """
    h = _text_hash(text)
    if h in _cache:
        log.info("normalization cache hit (hash=%s)", h[:12])
        return _cache[h]

    log.info("normalizing text via LLM (len=%d chars)", len(text))
    normalized = await generate_text(
        system=_SYSTEM_PROMPT,
        user=_USER_TEMPLATE.format(text=text),
    )
    _cache[h] = normalized
    log.info("normalization complete (input=%d, output=%d chars)", len(text), len(normalized))
    return normalized

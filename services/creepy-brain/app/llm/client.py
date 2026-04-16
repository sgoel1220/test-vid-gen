"""Anthropic client wrapper for structured and text generation."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may contain markdown fences or preamble."""
    m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    start = raw.find("{")
    if start >= 0:
        return raw[start:]
    return raw


async def _call_with_retry(
    messages: list[dict[str, Any]],
    system: str,
) -> str:
    """Make an Anthropic API call with retries on transient failures."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            response = await client.messages.create(
                model=settings.llm_model,
                max_tokens=8192,
                system=system,
                messages=messages,  # type: ignore[arg-type]
            )
            log.info(
                "llm call input_tokens=%d output_tokens=%d",
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            parts: list[str] = []
            for block in response.content:
                if hasattr(block, "text"):
                    parts.append(block.text)  # type: ignore[union-attr]
            return "".join(parts)
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                log.warning(
                    "API call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
            else:
                log.error("API call failed after %d attempts", MAX_RETRIES)

    assert last_exc is not None
    raise last_exc


async def generate_structured(
    system: str,
    user: str,
    response_model: Type[T],
) -> T:
    """Call Claude and parse the response into a Pydantic model."""
    json_instruction = (
        "\n\nIMPORTANT: You MUST respond with ONLY a valid JSON object. "
        "No preamble, no explanation, no markdown fences. Just raw JSON."
    )
    messages = [{"role": "user", "content": user}]
    raw = await _call_with_retry(messages, system + json_instruction)
    json_str = _extract_json(raw)
    return response_model.model_validate_json(json_str)


async def generate_text(system: str, user: str) -> str:
    """Call Claude and return raw prose text."""
    messages = [{"role": "user", "content": user}]
    return await _call_with_retry(messages, system)

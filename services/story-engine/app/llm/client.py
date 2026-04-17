"""Claude agent SDK wrapper for structured and text generation."""

from __future__ import annotations

import asyncio
import re
import structlog
from typing import Type, TypeVar

from pydantic import BaseModel

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may contain markdown fences or preamble."""
    # Try to find JSON in code fences first
    m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try to find raw JSON object
    start = raw.find("{")
    if start >= 0:
        return raw[start:]
    return raw


async def _query_with_retry(
    prompt: str, options: ClaudeAgentOptions
) -> list[str]:
    """Run a query with retries on transient SDK failures."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            text_parts: list[str] = []
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(message, ResultMessage):
                    log.info(
                        "llm call",
                        cost_usd=message.total_cost_usd,
                        tokens=message.usage,
                    )
            return text_parts
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log.warning(
                    "SDK query failed, retrying",
                    attempt=attempt + 1,
                    max_attempts=MAX_RETRIES,
                    delay_s=delay,
                    error=str(exc)[:200],
                )
                await asyncio.sleep(delay)
            else:
                log.error("SDK query failed", max_attempts=MAX_RETRIES)
    raise last_exc  # type: ignore[misc]


async def generate_structured(
    system: str,
    user: str,
    response_model: Type[T],
) -> T:
    """Call Claude and parse the response into a Pydantic model.

    Instructs the model to output JSON in the system prompt and parses
    with Pydantic. Does NOT use --json-schema CLI flag (unreliable).
    """
    json_instruction = (
        "\n\nIMPORTANT: You MUST respond with ONLY a valid JSON object. "
        "No preamble, no explanation, no markdown fences. Just raw JSON."
    )

    options = ClaudeAgentOptions(
        system_prompt=system + json_instruction,
        allowed_tools=[],
        max_turns=1,
    )

    text_parts = await _query_with_retry(prompt=user, options=options)
    raw = "".join(text_parts)
    json_str = _extract_json(raw)
    return response_model.model_validate_json(json_str)


async def generate_text(
    system: str,
    user: str,
) -> str:
    """Call Claude and return raw prose text."""
    options = ClaudeAgentOptions(
        system_prompt=system,
        allowed_tools=[],
        max_turns=1,
    )

    text_parts = await _query_with_retry(prompt=user, options=options)
    return "".join(text_parts)

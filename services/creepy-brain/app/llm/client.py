"""LLM client abstraction supporting Anthropic and OpenRouter."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol, Type, TypeVar

import httpx
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 5  # seconds


class LLMProvider(Protocol):
    """Minimal interface for an LLM backend."""

    async def complete(self, system: str, messages: list[dict[str, Any]]) -> str: ...


class AnthropicProvider:
    """Calls the Anthropic API using the official SDK."""

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic  # Lazy import - only needed when using Anthropic provider
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def complete(self, system: str, messages: list[dict[str, Any]]) -> str:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=8192,
            system=system,
            messages=messages,  # type: ignore[arg-type]
        )
        log.info(
            "llm call provider=anthropic input_tokens=%d output_tokens=%d",
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        parts: list[str] = []
        for block in response.content:
            if hasattr(block, "text"):
                parts.append(block.text)  # type: ignore[union-attr]
        return "".join(parts)


class OpenRouterProvider:
    """Calls the OpenRouter API (OpenAI-compatible REST) using httpx."""

    _BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._http = httpx.AsyncClient(timeout=120.0)

    async def complete(self, system: str, messages: list[dict[str, Any]]) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "system", "content": system}, *messages],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = await self._http.post(self._BASE_URL, json=payload, headers=headers)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        content: str = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        log.info(
            "llm call provider=openrouter model=%s input_tokens=%s output_tokens=%s",
            self._model,
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
        )
        return content


_provider: LLMProvider | None = None


def _get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        if settings.llm_provider == "openrouter":
            _provider = OpenRouterProvider(
                api_key=settings.openrouter_api_key,
                model=settings.llm_model,
            )
        else:
            _provider = AnthropicProvider(
                api_key=settings.anthropic_api_key,
                model=settings.llm_model,
            )
    return _provider


def _extract_json(raw: str) -> str:
    """Extract JSON from a response that may contain markdown fences or preamble."""
    m = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    start = raw.find("{")
    if start >= 0:
        return raw[start:]
    return raw


async def _call_with_retry(system: str, messages: list[dict[str, Any]]) -> str:
    """Call the configured LLM provider with retries on transient failures."""
    provider = _get_provider()
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            return await provider.complete(system, messages)
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2**attempt)
                log.warning(
                    "LLM call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
            else:
                log.error("LLM call failed after %d attempts", MAX_RETRIES)

    if last_exc is None:
        raise RuntimeError("retry loop exited without raising an exception")
    raise last_exc


async def generate_structured(
    system: str,
    user: str,
    response_model: Type[T],
) -> T:
    """Call the LLM and parse the response into a Pydantic model."""
    schema_str = str(response_model.model_json_schema())
    json_instruction = (
        "\n\nIMPORTANT: You MUST respond with ONLY a valid JSON object. "
        "No preamble, no explanation, no markdown fences. Just raw JSON.\n\n"
        f"The JSON MUST conform to this schema:\n{schema_str}"
    )
    full_system = system + json_instruction
    messages = [{"role": "user", "content": user}]

    last_exc: Exception | None = None
    for attempt in range(2):
        raw = await _call_with_retry(full_system, messages)
        extracted = _extract_json(raw)
        try:
            return response_model.model_validate_json(extracted)
        except Exception as exc:
            last_exc = exc
            log.error(
                "structured parse failed for %s (attempt %d), raw (first 2000): %s",
                response_model.__name__,
                attempt + 1,
                raw[:2000],
            )
    assert last_exc is not None
    raise last_exc


async def generate_text(system: str, user: str) -> str:
    """Call the LLM and return raw prose text."""
    return await _call_with_retry(system, [{"role": "user", "content": user}])

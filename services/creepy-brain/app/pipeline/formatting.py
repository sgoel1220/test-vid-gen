"""Formatting helpers shared by story pipeline stages."""

from __future__ import annotations

from collections.abc import Sequence

from app.pipeline.models import ActDraft


def format_act_drafts(
    acts: Sequence[ActDraft],
    *,
    empty_text: str | None = None,
) -> str:
    """Format act drafts with stable act headings for LLM context."""
    if not acts:
        return empty_text or ""

    parts: list[str] = []
    for act in acts:
        parts.append(f"--- Act {act.act_number}: {act.title} ---")
        parts.append(act.text)
        parts.append("")
    return "\n".join(parts)

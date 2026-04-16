"""Step 4: FULL STORY REVIEW — score assembled story, produce fix instructions."""

from __future__ import annotations

import logging

from app.llm import client
from app.llm.prompts import FULL_REVIEW_SYSTEM, FULL_REVIEW_USER
from app.models.act import ActDraft
from app.models.critique import FullStoryCritique
from app.models.outline import FiveActOutline
from app.models.story_bible import StoryBible

log = logging.getLogger(__name__)


def _assemble_full_text(acts: list[ActDraft]) -> str:
    parts: list[str] = []
    for act in acts:
        parts.append(f"--- Act {act.act_number}: {act.title} ---")
        parts.append(act.text)
        parts.append("")
    return "\n".join(parts)


async def review(
    bible: StoryBible,
    outline: FiveActOutline,
    acts: list[ActDraft],
) -> FullStoryCritique:
    """Review the full assembled story and score it."""
    full_text = _assemble_full_text(acts)
    log.info("full_reviewer: reviewing %d words", len(full_text.split()))

    user_prompt = FULL_REVIEW_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        outline_json=outline.model_dump_json(indent=2),
        full_text=full_text,
    )

    return await client.generate_structured(
        system=FULL_REVIEW_SYSTEM,
        user=user_prompt,
        response_model=FullStoryCritique,
    )

"""Step 2: OUTLINE REVIEW — check bible + outline for structural issues."""

from __future__ import annotations

import structlog

from app.llm import client
from app.llm.prompts import OUTLINE_REVIEW_SYSTEM, OUTLINE_REVIEW_USER
from app.models.critique import OutlineCritique
from app.models.outline import FiveActOutline
from app.models.story_bible import StoryBible

log = structlog.get_logger()


async def run(bible: StoryBible, outline: FiveActOutline) -> OutlineCritique:
    """Review the outline and return critique."""
    log.info("outline_reviewer: checking outline")
    user_prompt = OUTLINE_REVIEW_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        outline_json=outline.model_dump_json(indent=2),
    )
    return await client.generate_structured(
        system=OUTLINE_REVIEW_SYSTEM,
        user=user_prompt,
        response_model=OutlineCritique,
    )

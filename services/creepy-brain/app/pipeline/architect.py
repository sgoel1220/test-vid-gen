"""Step 1: ARCHITECT — premise → StoryBible + FiveActOutline."""

from __future__ import annotations

import logging

from app.llm import client
from app.llm.prompts import ARCHITECT_SYSTEM, ARCHITECT_USER
from app.pipeline.models import ArchitectOutput

log = logging.getLogger(__name__)


async def run(premise: str, target_word_count: int) -> ArchitectOutput:
    """Generate story bible and five-act outline from a premise."""
    log.info("architect: generating bible + outline (target %d words)", target_word_count)
    user_prompt = ARCHITECT_USER.format(
        premise=premise,
        target_word_count=target_word_count,
    )
    return await client.generate_structured(
        system=ARCHITECT_SYSTEM,
        user=user_prompt,
        response_model=ArchitectOutput,
    )

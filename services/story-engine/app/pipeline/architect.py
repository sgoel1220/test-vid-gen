"""Step 1: ARCHITECT — premise → StoryBible + FiveActOutline."""

from __future__ import annotations

import logging

from creepy_pasta_protocol.common import Frozen

from app.llm import client
from app.llm.prompts import ARCHITECT_SYSTEM, ARCHITECT_USER
from app.models.outline import FiveActOutline
from app.models.story_bible import StoryBible

log = logging.getLogger(__name__)


class ArchitectOutput(Frozen):
    bible: StoryBible
    outline: FiveActOutline


async def run(premise: str) -> ArchitectOutput:
    """Generate story bible and five-act outline from a premise."""
    log.info("architect: generating bible + outline")
    user_prompt = ARCHITECT_USER.format(premise=premise)
    return await client.generate_structured(
        system=ARCHITECT_SYSTEM,
        user=user_prompt,
        response_model=ArchitectOutput,
    )

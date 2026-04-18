"""Review steps: outline review, act inline check, full story review."""

from __future__ import annotations

import logging

from app.llm import client
from app.llm.prompts import (
    ACT_CHECK_SYSTEM,
    ACT_CHECK_USER,
    FULL_REVIEW_SYSTEM,
    FULL_REVIEW_USER,
    OUTLINE_REVIEW_SYSTEM,
    OUTLINE_REVIEW_USER,
)
from app.pipeline.formatting import format_act_drafts
from app.pipeline.models import (
    ActDraft,
    ActInlineCheck,
    ActOutline,
    FiveActOutline,
    FullStoryCritique,
    OutlineCritique,
    StoryBible,
)

log = logging.getLogger(__name__)


async def check_outline(bible: StoryBible, outline: FiveActOutline) -> OutlineCritique:
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


async def check_act(
    bible: StoryBible,
    act_outline: ActOutline,
    prior_acts: list[ActDraft],
    act_text: str,
) -> ActInlineCheck:
    """Run inline consistency check on a single act."""
    act_num = act_outline.act_number
    log.info("act_reviewer: checking act %d", act_num)

    prior_text = format_act_drafts(
        prior_acts,
        empty_text="(none — this is the first act)",
    )

    user_prompt = ACT_CHECK_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        act_outline_json=act_outline.model_dump_json(indent=2),
        prior_acts=prior_text,
        act_number=act_num,
        act_text=act_text,
    )
    return await client.generate_structured(
        system=ACT_CHECK_SYSTEM,
        user=user_prompt,
        response_model=ActInlineCheck,
    )


async def review_full_story(
    bible: StoryBible,
    outline: FiveActOutline,
    acts: list[ActDraft],
) -> FullStoryCritique:
    """Review the full assembled story and score it."""
    full_text = format_act_drafts(acts)

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

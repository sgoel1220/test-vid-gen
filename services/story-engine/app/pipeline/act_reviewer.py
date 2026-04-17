"""Step 3b: ACT INLINE CHECK — verify each act against its outline."""

from __future__ import annotations

import structlog

from app.llm import client
from app.llm.prompts import ACT_CHECK_SYSTEM, ACT_CHECK_USER
from app.models.act import ActDraft, ActInlineCheck
from app.models.outline import ActOutline
from app.models.story_bible import StoryBible

log = structlog.get_logger()


async def check_act(
    bible: StoryBible,
    act_outline: ActOutline,
    prior_acts: list[ActDraft],
    act_text: str,
) -> ActInlineCheck:
    """Run inline consistency check on a single act."""
    act_num = act_outline.act_number
    log.info("act_reviewer: checking act", act_num=act_num)

    prior_text = ""
    if prior_acts:
        parts: list[str] = []
        for a in prior_acts:
            parts.append(f"--- Act {a.act_number}: {a.title} ---")
            parts.append(a.text)
            parts.append("")
        prior_text = "\n".join(parts)
    else:
        prior_text = "(none — this is the first act)"

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

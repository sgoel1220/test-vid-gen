"""Step 3: WRITER — generate prose for a single act."""

from __future__ import annotations

import structlog

from app.llm import client
from app.llm.prompts import (
    ACT_REWRITE_SYSTEM,
    ACT_REWRITE_USER,
    WRITER_SYSTEM,
    WRITER_USER,
)
from app.models.act import ActDraft
from app.models.outline import ActOutline, FiveActOutline
from app.models.story_bible import StoryBible

log = structlog.get_logger()


def _format_beats(act_outline: ActOutline) -> str:
    return "\n".join(
        f"  - {b.description} ({b.purpose})" for b in act_outline.beats
    )


def _format_prior_acts(prior_acts: list[ActDraft]) -> str:
    if not prior_acts:
        return "(none — this is the first act)"
    parts: list[str] = []
    for act in prior_acts:
        parts.append(f"--- Act {act.act_number}: {act.title} ---")
        parts.append(act.text)
        parts.append("")
    return "\n".join(parts)


async def write_act(
    bible: StoryBible,
    outline: FiveActOutline,
    act_outline: ActOutline,
    prior_acts: list[ActDraft],
) -> ActDraft:
    """Generate prose for a single act."""
    act_num = act_outline.act_number
    log.info("writer: writing act", act_num=act_num)

    user_prompt = WRITER_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        outline_json=outline.model_dump_json(indent=2),
        prior_acts=_format_prior_acts(prior_acts),
        act_number=act_num,
        act_title=act_outline.title,
        target_word_count=act_outline.target_word_count,
        beats=_format_beats(act_outline),
        act_hook=act_outline.act_hook,
        act_cliffhanger=act_outline.act_cliffhanger,
    )

    text = await client.generate_text(system=WRITER_SYSTEM, user=user_prompt)
    text = text.strip()
    word_count = len(text.split())
    log.info("writer: act done", act_num=act_num, word_count=word_count)

    return ActDraft(
        act_number=act_num,
        title=act_outline.title,
        text=text,
        word_count=word_count,
    )


async def rewrite_act(
    bible: StoryBible,
    outline: FiveActOutline,
    act_outline: ActOutline,
    prior_acts: list[ActDraft],
    check_notes: str,
) -> ActDraft:
    """Rewrite an act that failed inline check."""
    act_num = act_outline.act_number
    log.info("writer: rewriting act", act_num=act_num)

    user_prompt = ACT_REWRITE_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        outline_json=outline.model_dump_json(indent=2),
        prior_acts=_format_prior_acts(prior_acts),
        act_number=act_num,
        act_title=act_outline.title,
        target_word_count=act_outline.target_word_count,
        beats=_format_beats(act_outline),
        act_hook=act_outline.act_hook,
        act_cliffhanger=act_outline.act_cliffhanger,
        check_notes=check_notes,
    )

    text = await client.generate_text(system=ACT_REWRITE_SYSTEM, user=user_prompt)
    text = text.strip()
    word_count = len(text.split())
    log.info("writer: act rewrite done", act_num=act_num, word_count=word_count)

    return ActDraft(
        act_number=act_num,
        title=act_outline.title,
        text=text,
        word_count=word_count,
    )

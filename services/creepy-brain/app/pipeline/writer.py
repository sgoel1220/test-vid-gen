"""Step 3: WRITER — generate prose for a single act."""

from __future__ import annotations

import logging

from app.llm import client
from app.llm.prompts import (
    ACT_REWRITE_SYSTEM,
    ACT_REWRITE_USER,
    WRITER_SYSTEM,
    WRITER_USER,
)
from app.pipeline.formatting import format_act_drafts
from app.pipeline.models import ActDraft, ActOutline, FiveActOutline, StoryBible

log = logging.getLogger(__name__)

_FIRST_ACT_EMPTY_TEXT = "(none — this is the first act)"


def _format_beats(act_outline: ActOutline) -> str:
    return "\n".join(
        f"  - {b.description} ({b.purpose})" for b in act_outline.beats
    )


async def _generate_act_draft(
    system: str,
    user_prompt: str,
    act_outline: ActOutline,
    label: str,
) -> ActDraft:
    """Generate text via LLM and return a typed ActDraft."""
    act_num = act_outline.act_number
    text = await client.generate_text(system=system, user=user_prompt)
    text = text.strip()
    word_count = len(text.split())
    log.info("writer: act %d %s done, %d words", act_num, label, word_count)
    return ActDraft(
        act_number=act_num,
        title=act_outline.title,
        text=text,
        word_count=word_count,
    )


async def write_act(
    bible: StoryBible,
    outline: FiveActOutline,
    act_outline: ActOutline,
    prior_acts: list[ActDraft],
    target_word_count: int,
) -> ActDraft:
    """Generate prose for a single act."""
    log.info("writer: writing act %d", act_outline.act_number)
    user_prompt = WRITER_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        outline_json=outline.model_dump_json(indent=2),
        prior_acts=format_act_drafts(prior_acts, empty_text=_FIRST_ACT_EMPTY_TEXT),
        act_number=act_outline.act_number,
        act_title=act_outline.title,
        target_word_count=target_word_count,
        beats=_format_beats(act_outline),
        act_hook=act_outline.act_hook,
        act_cliffhanger=act_outline.act_cliffhanger,
    )
    return await _generate_act_draft(WRITER_SYSTEM, user_prompt, act_outline, "write")


async def rewrite_act(
    bible: StoryBible,
    outline: FiveActOutline,
    act_outline: ActOutline,
    prior_acts: list[ActDraft],
    check_notes: str,
    target_word_count: int,
) -> ActDraft:
    """Rewrite an act that failed inline check."""
    log.info("writer: rewriting act %d", act_outline.act_number)
    user_prompt = ACT_REWRITE_USER.format(
        bible_json=bible.model_dump_json(indent=2),
        outline_json=outline.model_dump_json(indent=2),
        prior_acts=format_act_drafts(prior_acts, empty_text=_FIRST_ACT_EMPTY_TEXT),
        act_number=act_outline.act_number,
        act_title=act_outline.title,
        target_word_count=target_word_count,
        beats=_format_beats(act_outline),
        act_hook=act_outline.act_hook,
        act_cliffhanger=act_outline.act_cliffhanger,
        check_notes=check_notes,
    )
    return await _generate_act_draft(ACT_REWRITE_SYSTEM, user_prompt, act_outline, "rewrite")

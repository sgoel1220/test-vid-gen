"""Main pipeline orchestrator: premise → finished story.

Runs as a background asyncio task. Persists to Postgres via SQLAlchemy.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import client
from app.llm.prompts import (
    ARCHITECT_FIX_SYSTEM,
    ARCHITECT_FIX_USER,
    TARGETED_REWRITE_SYSTEM,
    TARGETED_REWRITE_USER,
)
from app.models.enums import StoryStatus
from app.pipeline import architect, reviewer, writer
from app.pipeline.formatting import format_act_drafts
from app.pipeline.models import ActDraft, ArchitectOutput
from app.services import story_service
from app.validation_limits import DEFAULT_STORY_TARGET_WORD_COUNT

log = logging.getLogger(__name__)

MAX_OUTLINE_LOOPS = 2
MAX_REVIEW_LOOPS = 3
PASSING_SCORE = 8.0


async def run_pipeline(
    story_id: uuid.UUID,
    premise: str,
    session: AsyncSession,
    target_word_count: int = DEFAULT_STORY_TARGET_WORD_COUNT,
) -> None:
    """Execute the full story generation pipeline.

    Persists progress to Postgres at each stage. Catches all exceptions
    and marks the story as failed if anything goes wrong.
    """
    try:
        await story_service.update_status(session, story_id, StoryStatus.GENERATING)
        await session.commit()

        # ── Step 1: Architect ────────────────────────────────────────
        arch_output = await architect.run(premise, target_word_count=target_word_count)
        bible = arch_output.bible
        outline = arch_output.outline

        await story_service.update_bible_and_outline(session, story_id, bible=bible, outline=outline)
        await session.commit()

        # ── Step 2: Outline review (max 2 loops) ────────────────────
        for outline_loop in range(MAX_OUTLINE_LOOPS):
            critique = await reviewer.check_outline(bible, outline)
            if critique.passes:
                log.info("outline passed on loop %d", outline_loop + 1)
                break

            log.info("outline failed, fixing (loop %d)", outline_loop + 1)
            fix_result = await client.generate_structured(
                system=ARCHITECT_FIX_SYSTEM,
                user=ARCHITECT_FIX_USER.format(
                    premise=premise,
                    bible_json=bible.model_dump_json(indent=2),
                    outline_json=outline.model_dump_json(indent=2),
                    fix_instructions=critique.fix_instructions,
                ),
                response_model=ArchitectOutput,
            )
            bible = fix_result.bible
            outline = fix_result.outline

            await story_service.update_bible_and_outline(session, story_id, bible=bible, outline=outline)
            await session.commit()

        # ── Step 3: Write acts + inline checks ──────────────────────
        acts: list[ActDraft] = []
        for act_outline in outline.acts:
            prior = list(acts)
            draft = await writer.write_act(bible, outline, act_outline, prior)

            check = await reviewer.check_act(bible, act_outline, prior, draft.text)
            if not check.passes:
                log.info("act %d failed inline check, rewriting", act_outline.act_number)
                draft = await writer.rewrite_act(
                    bible, outline, act_outline, prior, check.notes
                )

            acts.append(draft)

            await story_service.upsert_act(
                session,
                story_id,
                act_number=draft.act_number,
                title=draft.title,
                content=draft.text,
                word_count=draft.word_count,
            )
            await session.commit()

        # ── Step 4: Full story review loop ───────────────────────────
        await story_service.update_status(session, story_id, StoryStatus.REVIEWING)
        await session.commit()

        for review_loop in range(MAX_REVIEW_LOOPS):
            review = await reviewer.review_full_story(bible, outline, acts)
            score = review.scores.overall_score
            log.info("review loop %d: score=%.1f", review_loop + 1, score)

            if score >= PASSING_SCORE:
                log.info("story passed with score %.1f", score)
                break

            if not review.fix_instructions:
                log.info("no fix instructions despite low score, accepting")
                break

            full_text = format_act_drafts(acts)

            for fix in review.fix_instructions:
                act_idx = fix.act_number - 1
                if act_idx < 0 or act_idx >= len(acts):
                    continue
                act_outline = outline.acts[act_idx]

                log.info("rewriting act %d per review fix", fix.act_number)
                new_text = await client.generate_text(
                    system=TARGETED_REWRITE_SYSTEM,
                    user=TARGETED_REWRITE_USER.format(
                        bible_json=bible.model_dump_json(indent=2),
                        outline_json=outline.model_dump_json(indent=2),
                        full_text=full_text,
                        act_number=fix.act_number,
                        act_title=act_outline.title,
                        target_word_count=act_outline.target_word_count,
                        what_to_change=fix.what_to_change,
                        why=fix.why,
                    ),
                )
                new_text = new_text.strip()
                acts[act_idx] = ActDraft(
                    act_number=fix.act_number,
                    title=act_outline.title,
                    text=new_text,
                    word_count=len(new_text.split()),
                )
                await story_service.upsert_act(
                    session,
                    story_id,
                    act_number=fix.act_number,
                    title=act_outline.title,
                    content=new_text,
                    word_count=len(new_text.split()),
                )
                await session.commit()

        # ── Done ─────────────────────────────────────────────────────
        total_words = sum(a.word_count for a in acts)
        full_text = "\n\n".join(a.text for a in acts)
        await story_service.complete_story(session, story_id, full_text=full_text, word_count=total_words)
        await session.commit()
        log.info("pipeline complete for story %s", story_id)

    except Exception:
        log.exception("pipeline failed for story %s", story_id)
        try:
            await story_service.fail_story(session, story_id)
            await session.commit()
        except Exception:
            log.exception("failed to mark story %s as failed", story_id)

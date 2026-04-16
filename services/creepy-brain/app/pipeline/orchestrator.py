"""Main pipeline orchestrator: premise → finished story.

Runs as a background asyncio task. Persists to Postgres via SQLAlchemy.
"""

from __future__ import annotations

import logging
import traceback
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
from app.pipeline.models import ActDraft, ArchitectOutput
from app.services.story_service import StoryService

log = logging.getLogger(__name__)

MAX_OUTLINE_LOOPS = 2
MAX_REVIEW_LOOPS = 3
PASSING_SCORE = 8.0


async def run_pipeline(
    story_id: uuid.UUID,
    premise: str,
    session: AsyncSession,
) -> None:
    """Execute the full story generation pipeline.

    Persists progress to Postgres at each stage. Catches all exceptions
    and marks the story as failed if anything goes wrong.
    """
    svc = StoryService(session)

    try:
        await svc.update_status(story_id, StoryStatus.GENERATING)

        # ── Step 1: Architect ────────────────────────────────────────
        arch_output = await architect.run(premise)
        bible = arch_output.bible
        outline = arch_output.outline

        await svc.update_bible_and_outline(
            story_id,
            bible_json=bible.model_dump(mode="json"),
            outline_json=outline.model_dump(mode="json"),
            title=bible.title,
        )

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

            await svc.update_bible_and_outline(
                story_id,
                bible_json=bible.model_dump(mode="json"),
                outline_json=outline.model_dump(mode="json"),
                title=bible.title,
            )

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

            await svc.upsert_act(
                story_id,
                act_number=draft.act_number,
                title=draft.title,
                content=draft.text,
                word_count=draft.word_count,
            )

        # ── Step 4: Full story review loop ───────────────────────────
        await svc.update_status(story_id, StoryStatus.REVIEWING)

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

            full_text_parts: list[str] = []
            for a in acts:
                full_text_parts.append(f"--- Act {a.act_number}: {a.title} ---\n{a.text}\n")
            full_text = "\n".join(full_text_parts)

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
                await svc.upsert_act(
                    story_id,
                    act_number=fix.act_number,
                    title=act_outline.title,
                    content=new_text,
                    word_count=len(new_text.split()),
                )

        # ── Done ─────────────────────────────────────────────────────
        total_words = sum(a.word_count for a in acts)
        full_text = "\n\n".join(a.text for a in acts)
        await svc.complete_story(story_id, full_text=full_text, word_count=total_words)
        log.info("pipeline complete for story %s", story_id)

    except Exception:
        log.exception("pipeline failed for story %s", story_id)
        error_summary = traceback.format_exc()[-500:]
        try:
            await svc.fail_story(story_id, error=error_summary)
        except Exception:
            log.exception("failed to mark story %s as failed", story_id)

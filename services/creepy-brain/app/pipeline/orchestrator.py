"""Main pipeline orchestrator: premise → finished story.

Runs as a background asyncio task. Persists to Postgres via SQLAlchemy.
"""

from __future__ import annotations

import logging
import uuid
import importlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import client
from app.llm.prompts import (
    ARCHITECT_FIX_SYSTEM,
    ARCHITECT_FIX_USER,
    TARGETED_REWRITE_SYSTEM,
    TARGETED_REWRITE_USER,
)
from app.pipeline import architect, reviewer, writer
from app.pipeline.formatting import format_act_drafts
from app.pipeline.models import (
    ActDraft,
    ArchitectOutput,
    FiveActOutline,
    FixInstruction,
    OutlineCritique,
    StoryBible,
)
from app.validation_limits import ACT_WORD_COUNT_PROPORTIONS, DEFAULT_STORY_TARGET_WORD_COUNT

log = logging.getLogger(__name__)

MAX_OUTLINE_LOOPS = 2
MAX_REVIEW_LOOPS = 3
PASSING_SCORE = 8.0


class _StoryStatusValue(Protocol):
    value: str


class _StoryStatusEnum(Protocol):
    PENDING: _StoryStatusValue
    GENERATING: _StoryStatusValue
    REVIEWING: _StoryStatusValue
    COMPLETED: _StoryStatusValue
    FAILED: _StoryStatusValue


StoryStatus = cast(
    _StoryStatusEnum,
    importlib.import_module("app.models.enums").StoryStatus,
)


class _StoryService(Protocol):
    async def update_status(
        self,
        session: AsyncSession,
        story_id: uuid.UUID,
        status: _StoryStatusValue,
    ) -> None: ...

    async def update_bible_and_outline(
        self,
        session: AsyncSession,
        story_id: uuid.UUID,
        bible: StoryBible,
        outline: FiveActOutline,
        target_word_count: int,
    ) -> None: ...

    async def upsert_act(
        self,
        session: AsyncSession,
        story_id: uuid.UUID,
        act_number: int,
        title: str,
        content: str,
        word_count: int,
    ) -> None: ...

    async def complete_story(
        self,
        session: AsyncSession,
        story_id: uuid.UUID,
        full_text: str,
        word_count: int,
    ) -> None: ...

    async def fail_story(self, session: AsyncSession, story_id: uuid.UUID) -> None: ...


story_service = cast(
    _StoryService,
    importlib.import_module("app.services.story_service"),
)


class _ReviewDecision(Enum):
    ACCEPT = "accept"
    CONTINUE = "continue"
    STOP = "stop"


@dataclass
class _PipelineStory:
    id: uuid.UUID
    premise: str
    target_word_count: int
    status: _StoryStatusValue = StoryStatus.PENDING
    bible: StoryBible = field(init=False)
    outline: FiveActOutline = field(init=False)
    act_word_counts: list[int] = field(default_factory=list)
    acts: list[ActDraft] = field(default_factory=list)


def _derive_act_word_counts(total: int, num_acts: int) -> list[int]:
    """Distribute total word count across acts using fixed proportions."""
    proportions = ACT_WORD_COUNT_PROPORTIONS[:num_acts]
    norm = sum(proportions) or 1.0
    return [max(1, round(total * p / norm)) for p in proportions]


async def _transition_story_status(
    story: _PipelineStory,
    status: _StoryStatusValue,
    db: AsyncSession,
) -> None:
    story.status = status
    if isinstance(story, _PipelineStory):
        await story_service.update_status(db, story.id, status)
    await db.commit()


async def _run_architect(
    story: _PipelineStory,
    db: AsyncSession,
) -> tuple[StoryBible, FiveActOutline]:
    arch_output = await architect.run(
        story.premise,
        target_word_count=story.target_word_count,
    )
    story.bible = arch_output.bible
    story.outline = arch_output.outline
    return story.bible, story.outline


async def _persist_bible_and_outline(
    story: _PipelineStory,
    bible: StoryBible,
    outline: FiveActOutline,
    db: AsyncSession,
) -> None:
    await story_service.update_bible_and_outline(
        db,
        story.id,
        bible=bible,
        outline=outline,
        target_word_count=story.target_word_count,
    )
    await db.commit()


async def _repair_outline_loop(
    story: _PipelineStory,
    bible: StoryBible,
    outline: FiveActOutline,
    db: AsyncSession,
) -> tuple[StoryBible, FiveActOutline]:
    current_bible = bible
    current_outline = outline

    for outline_loop in range(MAX_OUTLINE_LOOPS):
        critique = await reviewer.check_outline(current_bible, current_outline)
        if critique.passes:
            log.info("outline passed on loop %d", outline_loop + 1)
            break

        log.info("outline failed, fixing (loop %d)", outline_loop + 1)
        current_bible, current_outline = await _request_architect_fix(
            story,
            current_bible,
            current_outline,
            critique,
        )
        await _persist_bible_and_outline(story, current_bible, current_outline, db)

    story.bible = current_bible
    story.outline = current_outline
    return current_bible, current_outline


async def _request_architect_fix(
    story: _PipelineStory,
    bible: StoryBible,
    outline: FiveActOutline,
    critique: OutlineCritique,
) -> tuple[StoryBible, FiveActOutline]:
    fix_result = await client.generate_structured(
        system=ARCHITECT_FIX_SYSTEM,
        user=ARCHITECT_FIX_USER.format(
            premise=story.premise,
            bible_json=bible.model_dump_json(indent=2),
            outline_json=outline.model_dump_json(indent=2),
            fix_instructions=critique.fix_instructions,
        ),
        response_model=ArchitectOutput,
    )
    return fix_result.bible, fix_result.outline


async def _write_act_with_review(
    story: _PipelineStory,
    act_num: int,
    prior_acts: list[ActDraft],
    word_count: int,
) -> ActDraft:
    act_outline = story.outline.acts[act_num - 1]
    draft = await writer.write_act(
        story.bible,
        story.outline,
        act_outline,
        prior_acts,
        word_count,
    )

    check = await reviewer.check_act(story.bible, act_outline, prior_acts, draft.text)
    if check.passes:
        return draft

    log.info("act %d failed inline check, rewriting", act_outline.act_number)
    return await writer.rewrite_act(
        story.bible,
        story.outline,
        act_outline,
        prior_acts,
        check.notes,
        word_count,
    )


async def _persist_act_draft(
    story: _PipelineStory,
    act_draft: ActDraft,
    db: AsyncSession,
) -> None:
    await story_service.upsert_act(
        db,
        story.id,
        act_number=act_draft.act_number,
        title=act_draft.title,
        content=act_draft.text,
        word_count=act_draft.word_count,
    )
    await db.commit()


async def _full_story_review_loop(
    story: _PipelineStory,
    acts: list[ActDraft],
    db: AsyncSession,
) -> list[ActDraft]:
    story.acts = acts

    for review_loop in range(MAX_REVIEW_LOOPS):
        loop_num = review_loop + 1
        review = await reviewer.review_full_story(story.bible, story.outline, acts)
        score = review.scores.overall_score
        log.info("review loop %d: score=%.1f", loop_num, score)

        decision = _evaluate_review_decision(score, loop_num)
        if decision is _ReviewDecision.ACCEPT:
            log.info("story passed with score %.1f", score)
            break
        if decision is _ReviewDecision.STOP:
            break

        if not review.fix_instructions:
            log.info("no fix instructions despite low score, accepting")
            break

        story.acts = list(acts)
        for fix in review.fix_instructions:
            act_idx = fix.act_number - 1
            if act_idx < 0 or act_idx >= len(acts):
                continue

            log.info("rewriting act %d per review fix", fix.act_number)
            rewritten = await _rewrite_act(story, acts[act_idx], fix, story.outline)
            acts[act_idx] = rewritten
            await _persist_act_draft(story, rewritten, db)
        story.acts = acts

    return acts


def _evaluate_review_decision(score: float, loop_num: int) -> _ReviewDecision:
    if score >= PASSING_SCORE:
        return _ReviewDecision.ACCEPT
    if loop_num >= MAX_REVIEW_LOOPS:
        return _ReviewDecision.STOP
    return _ReviewDecision.CONTINUE


async def _rewrite_act(
    story: _PipelineStory,
    act_draft: ActDraft,
    fix: FixInstruction,
    outline: FiveActOutline,
) -> ActDraft:
    act_idx = fix.act_number - 1
    act_outline = outline.acts[act_idx]
    full_text = format_act_drafts(story.acts)
    new_text = await client.generate_text(
        system=TARGETED_REWRITE_SYSTEM,
        user=TARGETED_REWRITE_USER.format(
            bible_json=story.bible.model_dump_json(indent=2),
            outline_json=outline.model_dump_json(indent=2),
            full_text=full_text,
            act_number=fix.act_number,
            act_title=act_outline.title,
            target_word_count=story.act_word_counts[act_idx],
            what_to_change=fix.what_to_change,
            why=fix.why,
        ),
    )
    new_text = new_text.strip()
    return ActDraft(
        act_number=act_draft.act_number,
        title=act_outline.title,
        text=new_text,
        word_count=len(new_text.split()),
    )


async def _finalize_story(
    story: _PipelineStory,
    acts: list[ActDraft],
    db: AsyncSession,
) -> None:
    total_words = sum(a.word_count for a in acts)
    full_text = "\n\n".join(a.text for a in acts)
    await story_service.complete_story(
        db,
        story.id,
        full_text=full_text,
        word_count=total_words,
    )
    await db.commit()
    log.info("pipeline complete for story %s", story.id)


async def _handle_pipeline_failure(
    story: _PipelineStory,
    exc: Exception,
    db: AsyncSession,
) -> None:
    log.exception("pipeline failed for story %s: %s", story.id, exc)
    try:
        await story_service.fail_story(db, story.id)
        await db.commit()
    except Exception:
        log.exception("failed to mark story %s as failed", story.id)


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
    story = _PipelineStory(
        id=story_id,
        premise=premise,
        target_word_count=target_word_count,
    )
    try:
        await _transition_story_status(story, StoryStatus.GENERATING, session)

        # ── Step 1: Architect ────────────────────────────────────────
        bible, outline = await _run_architect(story, session)

        await _persist_bible_and_outline(story, bible, outline, session)

        # ── Step 2: Outline review (max 2 loops) ────────────────────
        bible, outline = await _repair_outline_loop(story, bible, outline, session)

        # ── Step 3: Write acts + inline checks ──────────────────────
        act_word_counts = _derive_act_word_counts(target_word_count, len(outline.acts))
        story.act_word_counts = act_word_counts
        acts: list[ActDraft] = []
        story.acts = acts
        for idx, act_outline in enumerate(outline.acts):
            draft = await _write_act_with_review(
                story,
                act_outline.act_number,
                list(acts),
                act_word_counts[idx],
            )
            acts.append(draft)
            story.acts = acts
            await _persist_act_draft(story, draft, session)

        # ── Step 4: Full story review loop ───────────────────────────
        await _transition_story_status(story, StoryStatus.REVIEWING, session)
        acts = await _full_story_review_loop(story, acts, session)

        # ── Done ─────────────────────────────────────────────────────
        await _finalize_story(story, acts, session)

    except Exception as exc:
        await _handle_pipeline_failure(story, exc, session)

"""Unit tests for pipeline orchestrator helper extraction."""

from __future__ import annotations

import uuid
import warnings
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.models.enums import StoryStatus
from app.pipeline import orchestrator
from app.pipeline.models import (
    ActDraft,
    ActInlineCheck,
    ActOutline,
    ArchitectOutput,
    Beat,
    DimensionScore,
    FiveActOutline,
    FixInstruction,
    FullStoryCritique,
    HorrorRules,
    NarratorProfile,
    OutlineCritique,
    SettingDetail,
    StoryBible,
    Subplot,
    ForeshadowingSeed,
    TensionCurve,
)
from app.pipeline.orchestrator import (
    _derive_act_word_counts,
    _evaluate_review_decision,
    _finalize_story,
    _full_story_review_loop,
    _handle_pipeline_failure,
    _persist_act_draft,
    _persist_bible_and_outline,
    _repair_outline_loop,
    _request_architect_fix,
    _rewrite_act,
    _run_architect,
    _transition_story_status,
    _write_act_with_review,
    run_pipeline,
)

warnings.filterwarnings(
    "ignore",
    message="Unknown pytest.mark.asyncio.*",
    category=pytest.PytestUnknownMarkWarning,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _bible(title: str = "The Cellar Below") -> StoryBible:
    return StoryBible(
        title=title,
        logline="A narrator returns to a sealed cellar and finds it waiting.",
        narrator=NarratorProfile(
            name="Mara",
            age_range="30s",
            occupation="archivist",
            personality_traits=["careful", "haunted"],
            speech_patterns="plainspoken with precise details",
            reason_for_recounting="to warn the next owner",
        ),
        setting=SettingDetail(
            location="an inherited farmhouse",
            time_period="present day",
            atmosphere="wet, quiet, and tense",
            key_locations=["kitchen", "cellar", "orchard"],
            sensory_details="mold, rain, and distant knocking",
        ),
        horror_rules=HorrorRules(
            horror_subgenre="folk horror",
            threat_nature="a voice under the house",
            threat_rules="it answers only when lied to",
            escalation_pattern="each answer opens another locked door",
            what_is_at_stake="Mara's memory of her sister",
        ),
        subplots=[
            Subplot(
                name="missing sister",
                description="Mara's sister vanished in the house years ago.",
                introduced_in_act=1,
                resolved_in_act=2,
                connection_to_main_plot="the cellar voice knows what happened",
            )
        ],
        foreshadowing_seeds=[
            ForeshadowingSeed(
                planted_in_act=1,
                payoff_in_act=2,
                description="the kitchen clock stops whenever the cellar listens",
            )
        ],
        thematic_core="Grief makes bargains sound like answers.",
    )


def _beat(description: str = "Mara hears knocking below the floor") -> Beat:
    return Beat(description=description, purpose="escalation", emotional_tone="uneasy")


def _act_outline(act_number: int, title: str | None = None) -> ActOutline:
    return ActOutline(
        act_number=act_number,
        title=title or f"Act {act_number}",
        beats=[_beat(f"Key event for act {act_number}")],
        act_hook=f"Act {act_number} opens with a bad sign.",
        act_cliffhanger=f"Act {act_number} ends with a worse answer.",
        subplots_active=["missing sister"],
        tension_level=min(10, act_number + 4),
    )


def _outline(num_acts: int = 2, title_prefix: str = "Act") -> FiveActOutline:
    return FiveActOutline(
        acts=[
            _act_outline(act_number, f"{title_prefix} {act_number}")
            for act_number in range(1, num_acts + 1)
        ],
        tension_curve=TensionCurve(act_1=3, act_2=5, act_3=7, act_4=8, act_5=10),
        narrative_arc_summary="Mara opens the house and learns why it stayed shut.",
    )


def _story(
    *,
    premise: str = "A woman inherits a farmhouse with a locked cellar.",
    target_word_count: int = 1200,
    bible: StoryBible | None = None,
    outline: FiveActOutline | None = None,
) -> MagicMock:
    story = MagicMock()
    story.id = uuid.uuid4()
    story.premise = premise
    story.target_word_count = target_word_count
    story.bible = bible or _bible()
    story.outline = outline or _outline()
    story.act_word_counts = [target_word_count // len(story.outline.acts)] * len(story.outline.acts)
    story.acts = []
    return story


def _db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


def _draft(
    act_number: int = 1,
    *,
    title: str | None = None,
    text: str | None = None,
) -> ActDraft:
    draft_text = text or f"Act {act_number} draft text with several words."
    return ActDraft(
        act_number=act_number,
        title=title or f"Act {act_number}",
        text=draft_text,
        word_count=len(draft_text.split()),
    )


def _outline_critique(passes: bool, instructions: str = "Make the threat clearer.") -> OutlineCritique:
    return OutlineCritique(
        hooks_strong=passes,
        cliffhangers_effective=passes,
        subplot_integration=passes,
        payoff_setup=passes,
        tension_curve_valid=passes,
        passes=passes,
        fix_instructions=instructions,
    )


def _act_check(passes: bool, notes: str = "Keep the voice consistent.") -> ActInlineCheck:
    return ActInlineCheck(
        act_number=1,
        beats_matched=passes,
        voice_consistent=passes,
        contradictions=[] if passes else ["The cellar is both open and sealed."],
        pacing_ok=passes,
        passes=passes,
        notes=notes,
    )


def _fix(act_number: int = 1) -> FixInstruction:
    return FixInstruction(
        act_number=act_number,
        what_to_change="Tighten the ending and clarify the cellar bargain.",
        why="The payoff is currently too vague.",
    )


def _full_review(score: float, fixes: list[FixInstruction] | None = None) -> FullStoryCritique:
    return FullStoryCritique(
        scores=DimensionScore(
            subplot_completion=score,
            foreshadowing_payoff=score,
            character_consistency=score,
            pacing=score,
            ending_impact=score,
            overall_score=score,
        ),
        fix_instructions=fixes or [],
        summary="Review summary",
    )


def _decision_token(decision: Any) -> str:
    value = getattr(decision, "value", decision)
    return str(value).split(".")[-1].lower()


def _resolved_acts(result: Any, original: list[ActDraft]) -> list[ActDraft]:
    return original if result is None else result


class TestRunPipelineCurrentOrchestrator:
    async def test_generates_persists_reviews_and_completes_story(self) -> None:
        story_id = uuid.uuid4()
        premise = "A woman inherits a farmhouse with a locked cellar."
        target_word_count = 1200
        bible = _bible()
        outline = _outline(num_acts=2)
        drafts = [
            _draft(1, title=outline.acts[0].title, text="First act draft text."),
            _draft(2, title=outline.acts[1].title, text="Second act draft text."),
        ]
        db = _db()

        with (
            patch.object(orchestrator.story_service, "update_status", new_callable=AsyncMock) as update_status,
            patch.object(
                orchestrator.story_service,
                "update_bible_and_outline",
                new_callable=AsyncMock,
            ) as update_bible_and_outline,
            patch.object(orchestrator.story_service, "upsert_act", new_callable=AsyncMock) as upsert_act,
            patch.object(
                orchestrator.story_service,
                "complete_story",
                new_callable=AsyncMock,
            ) as complete_story,
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail_story,
            patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as architect_run,
            patch.object(orchestrator.reviewer, "check_outline", new_callable=AsyncMock) as check_outline,
            patch.object(orchestrator.writer, "write_act", new_callable=AsyncMock) as write_act,
            patch.object(orchestrator.reviewer, "check_act", new_callable=AsyncMock) as check_act,
            patch.object(
                orchestrator.reviewer,
                "review_full_story",
                new_callable=AsyncMock,
            ) as review_full_story,
            patch.object(orchestrator.writer, "rewrite_act", new_callable=AsyncMock) as rewrite_act,
        ):
            architect_run.return_value = ArchitectOutput(bible=bible, outline=outline)
            check_outline.return_value = _outline_critique(True)
            write_act.side_effect = drafts
            check_act.return_value = _act_check(True)
            review_full_story.return_value = _full_review(orchestrator.PASSING_SCORE)

            await run_pipeline(story_id, premise, db, target_word_count=target_word_count)

        update_status.assert_has_awaits(
            [
                call(db, story_id, StoryStatus.GENERATING),
                call(db, story_id, StoryStatus.REVIEWING),
            ]
        )
        architect_run.assert_awaited_once_with(premise, target_word_count=target_word_count)
        update_bible_and_outline.assert_awaited_once_with(
            db,
            story_id,
            bible=bible,
            outline=outline,
            target_word_count=target_word_count,
        )
        expected_word_counts = _derive_act_word_counts(target_word_count, len(outline.acts))
        assert write_act.await_args_list == [
            call(bible, outline, outline.acts[0], [], expected_word_counts[0]),
            call(bible, outline, outline.acts[1], [drafts[0]], expected_word_counts[1]),
        ]
        assert check_act.await_count == 2
        assert upsert_act.await_count == 2
        review_full_story.assert_awaited_once_with(bible, outline, drafts)
        complete_story.assert_awaited_once_with(
            db,
            story_id,
            full_text="First act draft text.\n\nSecond act draft text.",
            word_count=sum(draft.word_count for draft in drafts),
        )
        rewrite_act.assert_not_awaited()
        fail_story.assert_not_awaited()
        assert db.commit.await_count == 6

    async def test_repairs_failing_outline_through_structured_llm(self) -> None:
        story_id = uuid.uuid4()
        premise = "A woman inherits a farmhouse with a locked cellar."
        target_word_count = 1000
        bible = _bible()
        outline = _outline(num_acts=2)
        fixed_bible = _bible("Fixed Cellar")
        fixed_outline = _outline(num_acts=2, title_prefix="Fixed Act")
        drafts = [
            _draft(1, title=fixed_outline.acts[0].title),
            _draft(2, title=fixed_outline.acts[1].title),
        ]
        failing = _outline_critique(False, "Strengthen the midpoint.")
        db = _db()

        with (
            patch.object(orchestrator.story_service, "update_status", new_callable=AsyncMock),
            patch.object(
                orchestrator.story_service,
                "update_bible_and_outline",
                new_callable=AsyncMock,
            ) as update_bible_and_outline,
            patch.object(orchestrator.story_service, "upsert_act", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "complete_story", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail_story,
            patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as architect_run,
            patch.object(orchestrator.reviewer, "check_outline", new_callable=AsyncMock) as check_outline,
            patch.object(orchestrator.client, "generate_structured", new_callable=AsyncMock) as generate,
            patch.object(orchestrator.writer, "write_act", new_callable=AsyncMock) as write_act,
            patch.object(orchestrator.reviewer, "check_act", new_callable=AsyncMock) as check_act,
            patch.object(orchestrator.reviewer, "review_full_story", new_callable=AsyncMock) as review,
        ):
            architect_run.return_value = ArchitectOutput(bible=bible, outline=outline)
            check_outline.side_effect = [failing, _outline_critique(True)]
            generate.return_value = ArchitectOutput(bible=fixed_bible, outline=fixed_outline)
            write_act.side_effect = drafts
            check_act.return_value = _act_check(True)
            review.return_value = _full_review(orchestrator.PASSING_SCORE)

            await run_pipeline(story_id, premise, db, target_word_count=target_word_count)

        assert check_outline.await_args_list == [
            call(bible, outline),
            call(fixed_bible, fixed_outline),
        ]
        generate.assert_awaited_once()
        generate_kwargs = generate.await_args.kwargs
        assert generate_kwargs["system"] == orchestrator.ARCHITECT_FIX_SYSTEM
        assert generate_kwargs["response_model"] is ArchitectOutput
        assert premise in generate_kwargs["user"]
        assert failing.fix_instructions in generate_kwargs["user"]
        assert update_bible_and_outline.await_args_list == [
            call(
                db,
                story_id,
                bible=bible,
                outline=outline,
                target_word_count=target_word_count,
            ),
            call(
                db,
                story_id,
                bible=fixed_bible,
                outline=fixed_outline,
                target_word_count=target_word_count,
            ),
        ]
        assert write_act.await_args_list[0].args[:3] == (
            fixed_bible,
            fixed_outline,
            fixed_outline.acts[0],
        )
        fail_story.assert_not_awaited()

    async def test_rewrites_act_when_inline_review_fails(self) -> None:
        story_id = uuid.uuid4()
        premise = "A woman inherits a farmhouse with a locked cellar."
        target_word_count = 900
        bible = _bible()
        outline = _outline(num_acts=1)
        draft = _draft(1, title=outline.acts[0].title, text="Draft with a contradiction.")
        rewritten = _draft(1, title=outline.acts[0].title, text="Rewritten act with cleaner logic.")
        failing_check = _act_check(False, "Fix the contradiction.")
        db = _db()

        with (
            patch.object(orchestrator.story_service, "update_status", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "update_bible_and_outline", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "upsert_act", new_callable=AsyncMock) as upsert_act,
            patch.object(orchestrator.story_service, "complete_story", new_callable=AsyncMock) as complete_story,
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail_story,
            patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as architect_run,
            patch.object(orchestrator.reviewer, "check_outline", new_callable=AsyncMock) as check_outline,
            patch.object(orchestrator.writer, "write_act", new_callable=AsyncMock) as write_act,
            patch.object(orchestrator.reviewer, "check_act", new_callable=AsyncMock) as check_act,
            patch.object(orchestrator.writer, "rewrite_act", new_callable=AsyncMock) as rewrite_act,
            patch.object(orchestrator.reviewer, "review_full_story", new_callable=AsyncMock) as review,
        ):
            architect_run.return_value = ArchitectOutput(bible=bible, outline=outline)
            check_outline.return_value = _outline_critique(True)
            write_act.return_value = draft
            check_act.return_value = failing_check
            rewrite_act.return_value = rewritten
            review.return_value = _full_review(orchestrator.PASSING_SCORE)

            await run_pipeline(story_id, premise, db, target_word_count=target_word_count)

        rewrite_act.assert_awaited_once_with(
            bible,
            outline,
            outline.acts[0],
            [],
            failing_check.notes,
            target_word_count,
        )
        upsert_act.assert_awaited_once_with(
            db,
            story_id,
            act_number=rewritten.act_number,
            title=rewritten.title,
            content=rewritten.text,
            word_count=rewritten.word_count,
        )
        complete_story.assert_awaited_once_with(
            db,
            story_id,
            full_text=rewritten.text,
            word_count=rewritten.word_count,
        )
        fail_story.assert_not_awaited()

    async def test_applies_targeted_rewrite_during_full_story_review(self) -> None:
        story_id = uuid.uuid4()
        premise = "A woman inherits a farmhouse with a locked cellar."
        target_word_count = 1000
        bible = _bible()
        outline = _outline(num_acts=2)
        drafts = [
            _draft(1, title=outline.acts[0].title, text="First act remains."),
            _draft(2, title=outline.acts[1].title, text="Second act needs work."),
        ]
        fix = _fix(2)
        db = _db()

        with (
            patch.object(orchestrator.story_service, "update_status", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "update_bible_and_outline", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "upsert_act", new_callable=AsyncMock) as upsert_act,
            patch.object(orchestrator.story_service, "complete_story", new_callable=AsyncMock) as complete_story,
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail_story,
            patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as architect_run,
            patch.object(orchestrator.reviewer, "check_outline", new_callable=AsyncMock) as check_outline,
            patch.object(orchestrator.writer, "write_act", new_callable=AsyncMock) as write_act,
            patch.object(orchestrator.reviewer, "check_act", new_callable=AsyncMock) as check_act,
            patch.object(orchestrator.reviewer, "review_full_story", new_callable=AsyncMock) as review,
            patch.object(orchestrator.client, "generate_text", new_callable=AsyncMock) as generate_text,
        ):
            architect_run.return_value = ArchitectOutput(bible=bible, outline=outline)
            check_outline.return_value = _outline_critique(True)
            write_act.side_effect = drafts
            check_act.return_value = _act_check(True)
            review.side_effect = [_full_review(5.0, [fix]), _full_review(9.0)]
            generate_text.return_value = "  Rewritten second act with a clean reveal.  "

            await run_pipeline(story_id, premise, db, target_word_count=target_word_count)

        generate_text.assert_awaited_once()
        generate_kwargs = generate_text.await_args.kwargs
        assert generate_kwargs["system"] == orchestrator.TARGETED_REWRITE_SYSTEM
        assert fix.what_to_change in generate_kwargs["user"]
        assert outline.acts[1].title in generate_kwargs["user"]
        assert upsert_act.await_count == 3
        upsert_act.assert_has_awaits(
            [
                call(
                    db,
                    story_id,
                    act_number=2,
                    title=outline.acts[1].title,
                    content="Rewritten second act with a clean reveal.",
                    word_count=7,
                )
            ],
            any_order=True,
        )
        complete_story.assert_awaited_once_with(
            db,
            story_id,
            full_text="First act remains.\n\nRewritten second act with a clean reveal.",
            word_count=drafts[0].word_count + 7,
        )
        assert review.await_count == 2
        fail_story.assert_not_awaited()

    async def test_marks_story_failed_when_pipeline_dependency_raises(self) -> None:
        story_id = uuid.uuid4()
        premise = "A woman inherits a farmhouse with a locked cellar."
        db = _db()

        with (
            patch.object(orchestrator.story_service, "update_status", new_callable=AsyncMock) as update_status,
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail_story,
            patch.object(orchestrator.story_service, "complete_story", new_callable=AsyncMock) as complete_story,
            patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as architect_run,
        ):
            architect_run.side_effect = RuntimeError("architect unavailable")

            await run_pipeline(story_id, premise, db)

        update_status.assert_awaited_once_with(db, story_id, StoryStatus.GENERATING)
        fail_story.assert_awaited_once_with(db, story_id)
        complete_story.assert_not_awaited()
        assert db.commit.await_count == 2

    async def test_swallows_secondary_failure_while_marking_story_failed(self) -> None:
        story_id = uuid.uuid4()
        premise = "A woman inherits a farmhouse with a locked cellar."
        db = _db()
        db.commit.side_effect = [
            RuntimeError("initial commit failed"),
            RuntimeError("failure commit failed"),
        ]

        with (
            patch.object(orchestrator.story_service, "update_status", new_callable=AsyncMock),
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail_story,
        ):
            await run_pipeline(story_id, premise, db)

        fail_story.assert_awaited_once_with(db, story_id)
        assert db.commit.await_count == 2


class TestDeriveActWordCounts:
    def test_distributes_target_word_count_across_available_acts(self) -> None:
        counts = _derive_act_word_counts(1000, 2)

        assert counts == [459, 541]


# ---------------------------------------------------------------------------
# _transition_story_status
# ---------------------------------------------------------------------------


class TestTransitionStoryStatus:
    @pytest.mark.asyncio
    async def test_sets_story_status_and_commits(self) -> None:
        story = _story()
        db = _db()

        await _transition_story_status(story, StoryStatus.GENERATING, db)

        assert story.status == StoryStatus.GENERATING
        db.commit.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_propagates_commit_failure_after_setting_status(self) -> None:
        story = _story()
        db = _db()
        db.commit.side_effect = RuntimeError("commit failed")

        with pytest.raises(RuntimeError, match="commit failed"):
            await _transition_story_status(story, StoryStatus.FAILED, db)

        assert story.status == StoryStatus.FAILED
        db.commit.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# _run_architect
# ---------------------------------------------------------------------------


class TestRunArchitect:
    @pytest.mark.asyncio
    async def test_calls_architect_and_returns_bible_and_outline(self) -> None:
        bible = _bible()
        outline = _outline()
        story = _story(bible=bible, outline=outline, target_word_count=1500)
        db = _db()

        with patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as run:
            run.return_value = ArchitectOutput(bible=bible, outline=outline)

            result = await _run_architect(story, db)

        assert result == (bible, outline)
        run.assert_awaited_once_with(story.premise, target_word_count=story.target_word_count)
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_propagates_architect_failure(self) -> None:
        story = _story()
        db = _db()

        with patch.object(orchestrator.architect, "run", new_callable=AsyncMock) as run:
            run.side_effect = RuntimeError("architect unavailable")

            with pytest.raises(RuntimeError, match="architect unavailable"):
                await _run_architect(story, db)

        run.assert_awaited_once_with(story.premise, target_word_count=story.target_word_count)
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _persist_bible_and_outline
# ---------------------------------------------------------------------------


class TestPersistBibleAndOutline:
    @pytest.mark.asyncio
    async def test_persists_architect_output_and_commits(self) -> None:
        bible = _bible()
        outline = _outline()
        story = _story(bible=bible, outline=outline, target_word_count=1400)
        db = _db()

        with patch.object(
            orchestrator.story_service,
            "update_bible_and_outline",
            new_callable=AsyncMock,
        ) as update:
            await _persist_bible_and_outline(story, bible, outline, db)

        update.assert_awaited_once_with(
            db,
            story.id,
            bible=bible,
            outline=outline,
            target_word_count=story.target_word_count,
        )
        db.commit.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_does_not_commit_when_persistence_fails(self) -> None:
        bible = _bible()
        outline = _outline()
        story = _story(bible=bible, outline=outline)
        db = _db()

        with patch.object(
            orchestrator.story_service,
            "update_bible_and_outline",
            new_callable=AsyncMock,
        ) as update:
            update.side_effect = RuntimeError("outline write failed")

            with pytest.raises(RuntimeError, match="outline write failed"):
                await _persist_bible_and_outline(story, bible, outline, db)

        update.assert_awaited_once()
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _repair_outline_loop
# ---------------------------------------------------------------------------


class TestRepairOutlineLoop:
    @pytest.mark.asyncio
    async def test_repairs_failing_outline_until_reviewer_passes(self) -> None:
        bible = _bible()
        outline = _outline()
        fixed_bible = _bible("Fixed Cellar")
        fixed_outline = _outline(title_prefix="Fixed Act")
        story = _story(bible=bible, outline=outline)
        db = _db()
        failing = _outline_critique(False, "Strengthen the midpoint.")
        passing = _outline_critique(True)
        request_fix = AsyncMock(return_value=(fixed_bible, fixed_outline))
        persist = AsyncMock()

        with (
            patch.object(orchestrator.reviewer, "check_outline", new_callable=AsyncMock) as check,
            patch.object(orchestrator, "_request_architect_fix", new=request_fix),
            patch.object(orchestrator, "_persist_bible_and_outline", new=persist),
        ):
            check.side_effect = [failing, passing]

            result = await _repair_outline_loop(story, bible, outline, db)

        assert result == (fixed_bible, fixed_outline)
        assert check.await_args_list == [call(bible, outline), call(fixed_bible, fixed_outline)]
        request_fix.assert_awaited_once_with(story, bible, outline, failing)
        persist.assert_awaited_once_with(story, fixed_bible, fixed_outline, db)

    @pytest.mark.asyncio
    async def test_stops_after_max_outline_loops_and_returns_last_fix(self) -> None:
        bible = _bible()
        outline = _outline()
        fixed_once = (_bible("Fixed Once"), _outline(title_prefix="Once"))
        fixed_twice = (_bible("Fixed Twice"), _outline(title_prefix="Twice"))
        story = _story(bible=bible, outline=outline)
        db = _db()
        request_fix = AsyncMock(side_effect=[fixed_once, fixed_twice])
        persist = AsyncMock()

        with (
            patch.object(orchestrator, "MAX_OUTLINE_LOOPS", 2),
            patch.object(orchestrator.reviewer, "check_outline", new_callable=AsyncMock) as check,
            patch.object(orchestrator, "_request_architect_fix", new=request_fix),
            patch.object(orchestrator, "_persist_bible_and_outline", new=persist),
        ):
            check.side_effect = [
                _outline_critique(False, "First fix."),
                _outline_critique(False, "Second fix."),
            ]

            result = await _repair_outline_loop(story, bible, outline, db)

        assert result == fixed_twice
        assert check.await_count == 2
        assert request_fix.await_count == 2
        assert persist.await_count == 2


# ---------------------------------------------------------------------------
# _request_architect_fix
# ---------------------------------------------------------------------------


class TestRequestArchitectFix:
    @pytest.mark.asyncio
    async def test_calls_structured_llm_with_critique_and_returns_output(self) -> None:
        bible = _bible()
        outline = _outline()
        fixed_bible = _bible("Fixed House")
        fixed_outline = _outline(title_prefix="Fixed")
        story = _story(bible=bible, outline=outline)
        critique = _outline_critique(False, "Make the rules more concrete.")

        with patch.object(orchestrator.client, "generate_structured", new_callable=AsyncMock) as generate:
            generate.return_value = ArchitectOutput(bible=fixed_bible, outline=fixed_outline)

            result = await _request_architect_fix(story, bible, outline, critique)

        kwargs = generate.await_args.kwargs
        assert result == (fixed_bible, fixed_outline)
        assert kwargs["system"] == orchestrator.ARCHITECT_FIX_SYSTEM
        assert kwargs["response_model"] is ArchitectOutput
        assert story.premise in kwargs["user"]
        assert critique.fix_instructions in kwargs["user"]

    @pytest.mark.asyncio
    async def test_propagates_structured_llm_failure(self) -> None:
        bible = _bible()
        outline = _outline()
        story = _story(bible=bible, outline=outline)
        critique = _outline_critique(False)

        with patch.object(orchestrator.client, "generate_structured", new_callable=AsyncMock) as generate:
            generate.side_effect = RuntimeError("llm failed")

            with pytest.raises(RuntimeError, match="llm failed"):
                await _request_architect_fix(story, bible, outline, critique)

        generate.assert_awaited_once()


# ---------------------------------------------------------------------------
# _write_act_with_review
# ---------------------------------------------------------------------------


class TestWriteActWithReview:
    @pytest.mark.asyncio
    async def test_returns_writer_draft_when_inline_review_passes(self) -> None:
        outline = _outline(num_acts=2)
        story = _story(outline=outline)
        prior_acts: list[ActDraft] = []
        draft = _draft(1, title=outline.acts[0].title)

        with (
            patch.object(orchestrator.writer, "write_act", new_callable=AsyncMock) as write_act,
            patch.object(orchestrator.reviewer, "check_act", new_callable=AsyncMock) as check_act,
            patch.object(orchestrator.writer, "rewrite_act", new_callable=AsyncMock) as rewrite_act,
        ):
            write_act.return_value = draft
            check_act.return_value = _act_check(True)

            result = await _write_act_with_review(story, 1, prior_acts, 250)

        assert result == draft
        write_act.assert_awaited_once_with(story.bible, story.outline, outline.acts[0], prior_acts, 250)
        check_act.assert_awaited_once_with(story.bible, outline.acts[0], prior_acts, draft.text)
        rewrite_act.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rewrites_and_returns_rewrite_when_inline_review_fails(self) -> None:
        outline = _outline(num_acts=2)
        story = _story(outline=outline)
        prior_acts = [_draft(1, title=outline.acts[0].title)]
        draft = _draft(2, title=outline.acts[1].title)
        rewritten = _draft(2, title=outline.acts[1].title, text="Rewritten act with cleaner logic.")
        failing_check = _act_check(False, "Fix the contradiction.")

        with (
            patch.object(orchestrator.writer, "write_act", new_callable=AsyncMock) as write_act,
            patch.object(orchestrator.reviewer, "check_act", new_callable=AsyncMock) as check_act,
            patch.object(orchestrator.writer, "rewrite_act", new_callable=AsyncMock) as rewrite_act,
        ):
            write_act.return_value = draft
            check_act.return_value = failing_check
            rewrite_act.return_value = rewritten

            result = await _write_act_with_review(story, 2, prior_acts, 300)

        assert result == rewritten
        rewrite_act.assert_awaited_once_with(
            story.bible,
            story.outline,
            outline.acts[1],
            prior_acts,
            failing_check.notes,
            300,
        )


# ---------------------------------------------------------------------------
# _persist_act_draft
# ---------------------------------------------------------------------------


class TestPersistActDraft:
    @pytest.mark.asyncio
    async def test_upserts_act_and_commits(self) -> None:
        story = _story()
        db = _db()
        draft = _draft(1, text="Draft text to persist.")

        with patch.object(orchestrator.story_service, "upsert_act", new_callable=AsyncMock) as upsert:
            await _persist_act_draft(story, draft, db)

        upsert.assert_awaited_once_with(
            db,
            story.id,
            act_number=draft.act_number,
            title=draft.title,
            content=draft.text,
            word_count=draft.word_count,
        )
        db.commit.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_does_not_commit_when_upsert_fails(self) -> None:
        story = _story()
        db = _db()
        draft = _draft()

        with patch.object(orchestrator.story_service, "upsert_act", new_callable=AsyncMock) as upsert:
            upsert.side_effect = RuntimeError("upsert failed")

            with pytest.raises(RuntimeError, match="upsert failed"):
                await _persist_act_draft(story, draft, db)

        upsert.assert_awaited_once()
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _full_story_review_loop
# ---------------------------------------------------------------------------


class TestFullStoryReviewLoop:
    @pytest.mark.asyncio
    async def test_returns_without_rewrites_when_story_passes(self) -> None:
        story = _story()
        acts = [_draft(1), _draft(2)]
        db = _db()
        rewrite = AsyncMock()
        persist = AsyncMock()

        with (
            patch.object(orchestrator.reviewer, "review_full_story", new_callable=AsyncMock) as review,
            patch.object(orchestrator, "_rewrite_act", new=rewrite),
            patch.object(orchestrator, "_persist_act_draft", new=persist),
        ):
            review.return_value = _full_review(orchestrator.PASSING_SCORE)

            result = await _full_story_review_loop(story, acts, db)

        assert _resolved_acts(result, acts) == acts
        review.assert_awaited_once_with(story.bible, story.outline, acts)
        rewrite.assert_not_awaited()
        persist.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_applies_targeted_rewrite_then_accepts_passing_review(self) -> None:
        story = _story()
        acts = [_draft(1), _draft(2)]
        original_first = acts[0]
        rewritten = _draft(1, text="Rewritten first act with a sharper final image.")
        db = _db()
        rewrite = AsyncMock(return_value=rewritten)
        persist = AsyncMock()

        with (
            patch.object(orchestrator.reviewer, "review_full_story", new_callable=AsyncMock) as review,
            patch.object(orchestrator, "_rewrite_act", new=rewrite),
            patch.object(orchestrator, "_persist_act_draft", new=persist),
        ):
            review.side_effect = [_full_review(5.0, [_fix(1)]), _full_review(9.0)]

            result = await _full_story_review_loop(story, acts, db)

        final_acts = _resolved_acts(result, acts)
        assert final_acts[0] == rewritten
        rewrite.assert_awaited_once_with(story, original_first, _fix(1), story.outline)
        persist.assert_awaited_once_with(story, rewritten, db)
        assert review.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_invalid_fix_act_index(self) -> None:
        story = _story()
        acts = [_draft(1), _draft(2)]
        db = _db()
        rewrite = AsyncMock()
        persist = AsyncMock()

        with (
            patch.object(orchestrator, "MAX_REVIEW_LOOPS", 1),
            patch.object(orchestrator.reviewer, "review_full_story", new_callable=AsyncMock) as review,
            patch.object(orchestrator, "_rewrite_act", new=rewrite),
            patch.object(orchestrator, "_persist_act_draft", new=persist),
        ):
            review.return_value = _full_review(5.0, [_fix(99)])

            result = await _full_story_review_loop(story, acts, db)

        assert _resolved_acts(result, acts) == acts
        rewrite.assert_not_awaited()
        persist.assert_not_awaited()


# ---------------------------------------------------------------------------
# _evaluate_review_decision
# ---------------------------------------------------------------------------


class TestEvaluateReviewDecision:
    def test_accepts_passing_score(self) -> None:
        decision = _evaluate_review_decision(orchestrator.PASSING_SCORE, 1)

        assert _decision_token(decision) == "accept"

    def test_continues_low_score_before_max_loop(self) -> None:
        with patch.object(orchestrator, "MAX_REVIEW_LOOPS", 3):
            decision = _evaluate_review_decision(orchestrator.PASSING_SCORE - 1, 1)

        assert _decision_token(decision) == "continue"

    def test_stops_low_score_at_max_loop(self) -> None:
        with patch.object(orchestrator, "MAX_REVIEW_LOOPS", 3):
            decision = _evaluate_review_decision(orchestrator.PASSING_SCORE - 1, 3)

        assert _decision_token(decision) == "stop"


# ---------------------------------------------------------------------------
# _rewrite_act
# ---------------------------------------------------------------------------


class TestRewriteAct:
    @pytest.mark.asyncio
    async def test_calls_text_llm_and_returns_rewritten_act_draft(self) -> None:
        outline = _outline(num_acts=2)
        story = _story(outline=outline, target_word_count=1000)
        story.acts = [_draft(1, title=outline.acts[0].title), _draft(2, title=outline.acts[1].title)]
        story.act_word_counts = [450, 550]
        fix = _fix(2)

        with patch.object(orchestrator.client, "generate_text", new_callable=AsyncMock) as generate:
            generate.return_value = "  Rewritten second act with a clean reveal.  "

            result = await _rewrite_act(story, story.acts[1], fix, outline)

        kwargs = generate.await_args.kwargs
        assert result == ActDraft(
            act_number=2,
            title=outline.acts[1].title,
            text="Rewritten second act with a clean reveal.",
            word_count=7,
        )
        assert kwargs["system"] == orchestrator.TARGETED_REWRITE_SYSTEM
        assert fix.what_to_change in kwargs["user"]
        assert outline.acts[1].title in kwargs["user"]

    @pytest.mark.asyncio
    async def test_propagates_text_llm_failure(self) -> None:
        outline = _outline(num_acts=1)
        story = _story(outline=outline)
        draft = _draft(1, title=outline.acts[0].title)

        with patch.object(orchestrator.client, "generate_text", new_callable=AsyncMock) as generate:
            generate.side_effect = RuntimeError("rewrite failed")

            with pytest.raises(RuntimeError, match="rewrite failed"):
                await _rewrite_act(story, draft, _fix(1), outline)

        generate.assert_awaited_once()


# ---------------------------------------------------------------------------
# _finalize_story
# ---------------------------------------------------------------------------


class TestFinalizeStory:
    @pytest.mark.asyncio
    async def test_assembles_full_text_completes_story_and_commits(self) -> None:
        story = _story()
        acts = [_draft(1, text="First act text."), _draft(2, text="Second act text.")]
        db = _db()

        with patch.object(
            orchestrator.story_service,
            "complete_story",
            new_callable=AsyncMock,
        ) as complete:
            await _finalize_story(story, acts, db)

        complete.assert_awaited_once_with(
            db,
            story.id,
            full_text="First act text.\n\nSecond act text.",
            word_count=sum(act.word_count for act in acts),
        )
        db.commit.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_does_not_commit_when_completion_fails(self) -> None:
        story = _story()
        acts = [_draft()]
        db = _db()

        with patch.object(
            orchestrator.story_service,
            "complete_story",
            new_callable=AsyncMock,
        ) as complete:
            complete.side_effect = RuntimeError("completion failed")

            with pytest.raises(RuntimeError, match="completion failed"):
                await _finalize_story(story, acts, db)

        complete.assert_awaited_once()
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _handle_pipeline_failure
# ---------------------------------------------------------------------------


class TestHandlePipelineFailure:
    @pytest.mark.asyncio
    async def test_marks_story_failed_and_commits(self) -> None:
        story = _story()
        db = _db()
        exc = RuntimeError("pipeline failed")

        with (
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail,
            patch.object(orchestrator.log, "exception") as log_exception,
        ):
            await _handle_pipeline_failure(story, exc, db)

        fail.assert_awaited_once_with(db, story.id)
        db.commit.assert_awaited_once_with()
        assert log_exception.called

    @pytest.mark.asyncio
    async def test_swallows_secondary_failure_while_marking_failed(self) -> None:
        story = _story()
        db = _db()
        db.commit.side_effect = RuntimeError("commit failed")
        exc = RuntimeError("pipeline failed")

        with (
            patch.object(orchestrator.story_service, "fail_story", new_callable=AsyncMock) as fail,
            patch.object(orchestrator.log, "exception") as log_exception,
        ):
            await _handle_pipeline_failure(story, exc, db)

        fail.assert_awaited_once_with(db, story.id)
        db.commit.assert_awaited_once_with()
        assert log_exception.called

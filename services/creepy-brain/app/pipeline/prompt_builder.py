"""Build LLM prompt previews for pipeline stages without calling the LLM.

Use these functions to show the user exactly what prompts would be sent to
Claude so they can run them manually and feed the outputs back in.
"""

from __future__ import annotations

from app.llm.prompts import (
    ARCHITECT_SYSTEM,
    ARCHITECT_USER,
    FULL_REVIEW_SYSTEM,
    FULL_REVIEW_USER,
    WRITER_SYSTEM,
    WRITER_USER,
)
from app.schemas.prompts import PromptEntry, PromptPreviewResponse
from app.validation_limits import DEFAULT_STORY_TARGET_WORD_COUNT

_NUM_ACTS = 5

_STORY_INSTRUCTIONS = """\
These are the prompts the story pipeline would send to Claude in order.

HOW TO USE MANUALLY:
  1. Send the **Architect** prompt first (system + user).
     → Claude returns a JSON object with "bible" and "outline" keys.
  2. Copy that JSON and paste it into the Writer – Act N prompts
     where you see {bible_json} / {outline_json}.
     Fill in {prior_acts} with the acts you have already written
     (empty string for Act 1).
  3. Send each Writer prompt in order (Act 1 through Act 5).
     → Each response is raw prose for that act.
  4. Paste all five acts (joined with two newlines) into the
     **Full Review** prompt where you see {full_text}.
  5. The reviewer returns a JSON score + optional revision requests.
     You can skip this step or act on its suggestions manually.

After collecting all act texts, concatenate them and submit the workflow
with manual_story_text set to the combined result to bypass LLM generation.
"""


def build_story_prompts(
    premise: str,
    target_word_count: int = DEFAULT_STORY_TARGET_WORD_COUNT,
) -> PromptPreviewResponse:
    """Return all prompts the story pipeline would use, in execution order.

    The Architect prompt is fully substituted and ready to copy-paste.
    Writer and Reviewer prompts are templates: placeholders like {bible_json}
    must be filled with the output from the previous step.
    """
    per_act_words = target_word_count // _NUM_ACTS
    entries: list[PromptEntry] = []

    # -- 1. Architect (fully substituted, ready to run) ----------------------
    entries.append(
        PromptEntry(
            label="Architect",
            system=ARCHITECT_SYSTEM,
            user=ARCHITECT_USER.format(
                premise=premise,
                target_word_count=target_word_count,
            ),
            notes=(
                "Run this first. The response is a JSON object with 'bible' and 'outline' keys. "
                "You will need both for the Writer prompts."
            ),
            is_template=False,
        )
    )

    # -- 2. Writer – one prompt per act (templates) --------------------------
    for act_num in range(1, _NUM_ACTS + 1):
        entries.append(
            PromptEntry(
                label=f"Writer – Act {act_num}",
                system=WRITER_SYSTEM,
                user=WRITER_USER.format(
                    bible_json="{bible_json}",
                    outline_json="{outline_json}",
                    prior_acts="{prior_acts}",
                    act_number=act_num,
                    act_title=f"{{act_{act_num}_title}}",
                    target_word_count=per_act_words,
                    beats=f"{{act_{act_num}_beats}}",
                    act_hook=f"{{act_{act_num}_hook}}",
                    act_cliffhanger=f"{{act_{act_num}_cliffhanger}}",
                ),
                notes=(
                    f"Fill {{bible_json}} and {{outline_json}} from the Architect output. "
                    f"Fill {{prior_acts}} with the text of Acts 1–{act_num - 1} "
                    f"(leave empty for Act 1). "
                    f"Fill act_{act_num}_title / beats / hook / cliffhanger from the outline."
                ),
                is_template=True,
            )
        )

    # -- 3. Full Review (template) -------------------------------------------
    entries.append(
        PromptEntry(
            label="Full Review",
            system=FULL_REVIEW_SYSTEM,
            user=FULL_REVIEW_USER.format(
                bible_json="{bible_json}",
                outline_json="{outline_json}",
                full_text="{full_text}",
            ),
            notes=(
                "Optional. Fill {bible_json} and {outline_json} from the Architect output. "
                "Fill {full_text} with all five acts concatenated. "
                "The reviewer returns a JSON score and optional revision notes. "
                "You can skip this or use the feedback to revise manually."
            ),
            is_template=True,
        )
    )

    return PromptPreviewResponse(
        stage="story",
        prompts=entries,
        instructions=_STORY_INSTRUCTIONS,
    )

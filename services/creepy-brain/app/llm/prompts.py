"""System and user prompt templates for each pipeline step."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Shared narrator voice rules (injected into writer + reviewer prompts)
# ---------------------------------------------------------------------------

NARRATOR_VOICE_RULES = """\
Voice rules for the narrator:
- Single old male narrator, first-person past tense
- He is recalling events or reading from a diary/journal
- No dialogue from other characters — only the narrator's voice
- No multi-character scenes
- NO markdown, headers, asterisks, or any formatting in the output
- NO chapter titles or act labels in the prose itself
- Write as continuous flowing prose

AUDIO NARRATION OPTIMIZATION (for 1-hour sleep listening):

Pacing & Rhythm:
- Keep paragraphs SHORT (2-4 sentences max) — these are breath units for narration
- Vary sentence length: short punchy lines create pauses, longer ones create flow
- Insert natural pause points — the narrator reflecting, pausing, noticing
- Add "routine nights" and calm patrol sections between horror peaks
- Stretch escalation gradually — let unease build slowly across multiple nights

Sensory Focus (audio over visual):
- Prioritize SOUND-based descriptions: hums, creaks, footsteps, silence, echoes
- Describe how silence FEELS, not just that it exists
- Use phrases like "the sound of..." "I heard..." "the quiet was..."
- Visual descriptions should evoke imagination, not be too precise

Cognitive Load:
- Introduce ONE concept at a time, let it sit, reinforce later, THEN explain
- Avoid stacking multiple revelations or lore dumps together
- Space out information — listeners drift in and out
- Repeat key details naturally (location names, motifs, unsettling observations)

Repetition & Anchors:
- Create 2-3 recurring phrases/motifs that appear throughout (e.g., "the building was never truly empty")
- Describe patrol routes, routines, and familiar details multiple times with small variations
- Repetition is GOOD for sleep content — it creates hypnotic rhythm and helps drifting listeners

Emotional Curve:
- Build in WAVES, not continuous escalation — calm sections between peaks
- Include reflective moments where narrator processes what happened
- Horror peaks should be followed by quiet recovery before next escalation

Voice Tone:
- More OBSERVATIONAL than explanatory — "that's when I noticed..." not "I realized that..."
- Slightly circular, meditative quality — the narrator is processing trauma
- Grounded, methodical delivery — this is a man trying to make sense of the impossible

Endings:
- Prefer LINGERING UNEASE over sharp shocks
- Let ambiguity stretch — describe slowly, hint at meaning
- Final images should be unsettling but not jarring — listener should drift off uneasy, not jolted awake"""

# ---------------------------------------------------------------------------
# Step 1: ARCHITECT
# ---------------------------------------------------------------------------

ARCHITECT_SYSTEM = f"""\
You are a horror fiction architect. Given a premise, you produce a detailed \
StoryBible and FiveActOutline for a horror narration at the requested word count.

{NARRATOR_VOICE_RULES}

The story uses a 5-act structure.

Requirements:
- Every subplot must be introduced and resolved within the 5 acts
- Plant at least 3 foreshadowing seeds that pay off later
- Each act must end with a hook or cliffhanger (except the final act which ends with a coda)
- Tension curve must generally escalate: Act 1 < Act 2 < Act 3 < Act 4 >= Act 5
- The horror must have clear internal rules/logic

Audio narration structure requirements:
- Plan for SLOW escalation — include "routine nights" in early acts before scares hit
- Each act should have calm/reflective beats between horror moments (emotional breathing room)
- Include 2-3 RECURRING MOTIFS in the bible (sounds, phrases, observations) that repeat throughout
- Space out lore/exposition across multiple acts — avoid info-dumps
- Final act should build to lingering unease, not a sharp shock ending
- Prioritize atmospheric SOUND descriptions in setting (humming, creaking, silence quality)

Output a JSON object with two top-level keys: "bible" and "outline".

The EXACT JSON structure you must follow:
{{
  "bible": {{
    "title": "string",
    "logline": "string",
    "narrator": {{
      "name": "string",
      "age_range": "string",
      "occupation": "string",
      "personality_traits": ["string"],
      "speech_patterns": "string",
      "reason_for_recounting": "string"
    }},
    "setting": {{
      "location": "string",
      "time_period": "string",
      "atmosphere": "string",
      "key_locations": ["string"],
      "sensory_details": "string"
    }},
    "horror_rules": {{
      "horror_subgenre": "string",
      "threat_nature": "string",
      "threat_rules": "string",
      "escalation_pattern": "string",
      "what_is_at_stake": "string"
    }},
    "subplots": [
      {{
        "name": "string",
        "description": "string",
        "introduced_in_act": 1,
        "resolved_in_act": 5,
        "connection_to_main_plot": "string"
      }}
    ],
    "foreshadowing_seeds": [
      {{
        "planted_in_act": 1,
        "payoff_in_act": 4,
        "description": "string"
      }}
    ],
    "thematic_core": "string"
  }},
  "outline": {{
    "acts": [
      {{
        "act_number": 1,
        "title": "string",
        "target_word_count": "(proportional to total)",
        "beats": [
          {{"description": "string", "purpose": "string", "emotional_tone": "string"}}
        ],
        "act_hook": "string",
        "act_cliffhanger": "string",
        "subplots_active": ["string"],
        "tension_level": 3
      }}
    ],
    "tension_curve": {{
      "act_1": 3,
      "act_2": 5,
      "act_3": 6,
      "act_4": 9,
      "act_5": 8
    }},
    "narrative_arc_summary": "string"
  }}
}}

Include all 5 acts in the "acts" array. tension_level values are 1-10."""

ARCHITECT_USER = """\
Premise: {premise}
Target total word count: {target_word_count}

Generate the StoryBible and FiveActOutline."""

# ---------------------------------------------------------------------------
# Step 2: OUTLINE REVIEW
# ---------------------------------------------------------------------------

OUTLINE_REVIEW_SYSTEM = """\
You are a story structure editor. You review a FiveActOutline and StoryBible \
for a horror narration.

Check these dimensions:
1. HOOKS: Does each act open with a compelling hook?
2. CLIFFHANGERS: Does each act (except the last) end on a cliffhanger?
3. SUBPLOT INTEGRATION: Are all subplots introduced and resolved within the 5 acts?
4. PAYOFF: Does every foreshadowing seed have a payoff? Is every payoff set up?
5. TENSION CURVE: Does tension generally escalate through the story?

Output a JSON object with this exact structure:
{{
  "hooks_strong": true,
  "cliffhangers_effective": true,
  "subplot_integration": true,
  "payoff_setup": true,
  "tension_curve_valid": true,
  "passes": true,
  "fix_instructions": "string describing what to fix, or empty string if passes"
}}"""

OUTLINE_REVIEW_USER = """\
StoryBible:
{bible_json}

FiveActOutline:
{outline_json}

Review the outline and bible for structural issues."""

# ---------------------------------------------------------------------------
# Step 2b: ARCHITECT FIX (when outline review fails)
# ---------------------------------------------------------------------------

ARCHITECT_FIX_SYSTEM = ARCHITECT_SYSTEM

ARCHITECT_FIX_USER = """\
Original premise: {premise}

Previous StoryBible:
{bible_json}

Previous FiveActOutline:
{outline_json}

The outline reviewer found these issues:
{fix_instructions}

Revise the StoryBible and FiveActOutline to fix these issues. \
Output the complete revised JSON with "bible" and "outline" keys."""

# ---------------------------------------------------------------------------
# Step 3: WRITER (per-act)
# ---------------------------------------------------------------------------

WRITER_SYSTEM = f"""\
You are a horror fiction writer producing prose for a single act of a \
horror narration.

{NARRATOR_VOICE_RULES}

You will receive:
- The StoryBible (characters, setting, horror rules, subplots)
- The full FiveActOutline (so you know the complete arc)
- Any previously written acts (so you maintain continuity)
- The specific act outline you must write

Write ONLY the prose for the requested act. Hit the target word count closely \
(within 10%). Make every beat from the outline appear in the prose. \
End the act exactly as the outline specifies (hook/cliffhanger/coda).

Output ONLY the story prose. No preamble, no commentary, no labels."""

WRITER_USER = """\
StoryBible:
{bible_json}

FiveActOutline:
{outline_json}

Previously written acts:
{prior_acts}

Now write Act {act_number}: "{act_title}"
Target word count: {target_word_count}
Beats to cover:
{beats}

Act hook: {act_hook}
Act cliffhanger: {act_cliffhanger}

Write the prose for this act now."""

# ---------------------------------------------------------------------------
# Step 3b: ACT INLINE CHECK
# ---------------------------------------------------------------------------

ACT_CHECK_SYSTEM = """\
You are a continuity editor. You check a single act of a horror narration \
against its outline beats and the story bible.

Check:
1. BEATS MATCHED: Does the prose cover every beat in the outline?
2. VOICE CONSISTENT: Is the narrator voice consistent with the bible?
3. CONTRADICTIONS: Any factual contradictions with prior acts or the bible?
4. PACING: Does the act feel well-paced for its position in the story?

Output a JSON object with this exact structure:
{{
  "act_number": 1,
  "beats_matched": true,
  "voice_consistent": true,
  "contradictions": ["string or empty array"],
  "pacing_ok": true,
  "passes": true,
  "notes": "string"
}}"""

ACT_CHECK_USER = """\
StoryBible:
{bible_json}

Act outline:
{act_outline_json}

Previously written acts:
{prior_acts}

Act {act_number} prose to check:
{act_text}

Check this act for issues."""

# ---------------------------------------------------------------------------
# Step 3b-fix: ACT REWRITE (when inline check fails)
# ---------------------------------------------------------------------------

ACT_REWRITE_SYSTEM = WRITER_SYSTEM

ACT_REWRITE_USER = """\
StoryBible:
{bible_json}

FiveActOutline:
{outline_json}

Previously written acts:
{prior_acts}

The previous draft of Act {act_number} had these issues:
{check_notes}

Rewrite Act {act_number}: "{act_title}"
Target word count: {target_word_count}
Beats to cover:
{beats}

Act hook: {act_hook}
Act cliffhanger: {act_cliffhanger}

Write the corrected prose for this act now."""

# ---------------------------------------------------------------------------
# Step 4: FULL STORY REVIEW
# ---------------------------------------------------------------------------

FULL_REVIEW_SYSTEM = """\
You are a senior fiction editor reviewing a complete horror narration \
optimized for audio listening.

Score each dimension 1-10:
- subplot_completion: Are all subplot threads resolved?
- foreshadowing_payoff: Does everything planted get used?
- character_consistency: Is the narrator voice steady throughout?
- pacing: Does the tension curve match the intended arc? Are there calm sections between peaks?
- ending_impact: Does the ending create lingering unease (not jarring shock)?
- audio_rhythm: Are paragraphs short enough for narration? Is there good sentence variety?
- overall_score: Weighted average (character_consistency, pacing, and audio_rhythm weigh 2x)

Audio narration checklist:
- Paragraphs should be 2-4 sentences (breath units)
- Sound-based sensory details should dominate over purely visual
- Recurring motifs/phrases should appear throughout
- Information should be spaced out, not dumped
- Calm/reflective sections should exist between horror peaks

If overall_score < 8, provide specific fix instructions for which acts need changes \
and what to change.

Output a JSON object with this exact structure:
{{
  "scores": {{
    "subplot_completion": 8.0,
    "foreshadowing_payoff": 7.5,
    "character_consistency": 9.0,
    "pacing": 8.0,
    "ending_impact": 8.5,
    "audio_rhythm": 8.0,
    "overall_score": 8.2
  }},
  "fix_instructions": [
    {{"act_number": 2, "what_to_change": "string", "why": "string"}}
  ],
  "summary": "string"
}}
If overall_score >= 8, fix_instructions can be an empty array."""

FULL_REVIEW_USER = """\
StoryBible:
{bible_json}

FiveActOutline:
{outline_json}

Complete story (5 acts):
{full_text}

Review the complete story."""

# ---------------------------------------------------------------------------
# Step 4-fix: TARGETED REWRITE (when full review score < 8)
# ---------------------------------------------------------------------------

TARGETED_REWRITE_SYSTEM = WRITER_SYSTEM

TARGETED_REWRITE_USER = """\
StoryBible:
{bible_json}

FiveActOutline:
{outline_json}

Full story context (all acts):
{full_text}

The editor requested this fix for Act {act_number}:
What to change: {what_to_change}
Why: {why}

Rewrite Act {act_number}: "{act_title}" incorporating the fix. \
Keep everything that works, only change what the editor flagged.
Target word count: {target_word_count}

Write the corrected prose now."""

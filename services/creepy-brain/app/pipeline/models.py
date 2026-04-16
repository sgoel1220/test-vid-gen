"""Internal Pydantic models for the story generation pipeline.

These models represent LLM structured outputs and pipeline data transfer objects.
They are NOT SQLAlchemy ORM models.
"""

from __future__ import annotations

from pydantic import BaseModel


class Frozen(BaseModel):
    """Immutable base model for pipeline data."""

    model_config = {"frozen": True, "extra": "forbid"}


# ---------------------------------------------------------------------------
# Story Bible Models
# ---------------------------------------------------------------------------


class NarratorProfile(Frozen):
    name: str
    age_range: str
    occupation: str
    personality_traits: list[str]
    speech_patterns: str
    reason_for_recounting: str


class SettingDetail(Frozen):
    location: str
    time_period: str
    atmosphere: str
    key_locations: list[str]
    sensory_details: str


class HorrorRules(Frozen):
    horror_subgenre: str
    threat_nature: str
    threat_rules: str
    escalation_pattern: str
    what_is_at_stake: str


class Subplot(Frozen):
    name: str
    description: str
    introduced_in_act: int
    resolved_in_act: int
    connection_to_main_plot: str


class ForeshadowingSeed(Frozen):
    planted_in_act: int
    payoff_in_act: int
    description: str


class StoryBible(Frozen):
    title: str
    logline: str
    narrator: NarratorProfile
    setting: SettingDetail
    horror_rules: HorrorRules
    subplots: list[Subplot]
    foreshadowing_seeds: list[ForeshadowingSeed]
    thematic_core: str


# ---------------------------------------------------------------------------
# Outline Models
# ---------------------------------------------------------------------------


class Beat(Frozen):
    description: str
    purpose: str
    emotional_tone: str


class ActOutline(Frozen):
    act_number: int
    title: str
    target_word_count: int
    beats: list[Beat]
    act_hook: str
    act_cliffhanger: str
    subplots_active: list[str]
    tension_level: int


class TensionCurve(Frozen):
    act_1: int
    act_2: int
    act_3: int
    act_4: int
    act_5: int


class FiveActOutline(Frozen):
    acts: list[ActOutline]
    tension_curve: TensionCurve
    narrative_arc_summary: str


# ---------------------------------------------------------------------------
# Act Draft Models
# ---------------------------------------------------------------------------


class ActDraft(Frozen):
    act_number: int
    title: str
    text: str
    word_count: int


class ActInlineCheck(Frozen):
    act_number: int
    beats_matched: bool
    voice_consistent: bool
    contradictions: list[str]
    pacing_ok: bool
    passes: bool
    notes: str


# ---------------------------------------------------------------------------
# Critique Models
# ---------------------------------------------------------------------------


class OutlineCritique(Frozen):
    hooks_strong: bool
    cliffhangers_effective: bool
    subplot_integration: bool
    payoff_setup: bool
    tension_curve_valid: bool
    passes: bool
    fix_instructions: str


class DimensionScore(Frozen):
    subplot_completion: float
    foreshadowing_payoff: float
    character_consistency: float
    pacing: float
    ending_impact: float
    audio_rhythm: float = 8.0
    overall_score: float


class FixInstruction(Frozen):
    act_number: int
    what_to_change: str
    why: str


class FullStoryCritique(Frozen):
    scores: DimensionScore
    fix_instructions: list[FixInstruction]
    summary: str


# ---------------------------------------------------------------------------
# Architect Output
# ---------------------------------------------------------------------------


class ArchitectOutput(Frozen):
    bible: StoryBible
    outline: FiveActOutline

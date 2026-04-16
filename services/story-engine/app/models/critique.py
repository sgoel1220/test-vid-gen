"""Critique models for outline review and full story review."""

from __future__ import annotations

from typing import List

from creepy_pasta_protocol.common import Frozen


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
    audio_rhythm: float = 8.0  # default for backward compat
    overall_score: float


class FixInstruction(Frozen):
    act_number: int
    what_to_change: str
    why: str


class FullStoryCritique(Frozen):
    scores: DimensionScore
    fix_instructions: List[FixInstruction]
    summary: str

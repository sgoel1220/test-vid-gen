"""Five-act outline models."""

from __future__ import annotations

from typing import List

from creepy_pasta_protocol.common import Frozen


class Beat(Frozen):
    description: str
    purpose: str
    emotional_tone: str


class ActOutline(Frozen):
    act_number: int
    title: str
    target_word_count: int
    beats: List[Beat]
    act_hook: str
    act_cliffhanger: str
    subplots_active: List[str]
    tension_level: int


class TensionCurve(Frozen):
    act_1: int
    act_2: int
    act_3: int
    act_4: int
    act_5: int


class FiveActOutline(Frozen):
    acts: List[ActOutline]
    tension_curve: TensionCurve
    narrative_arc_summary: str

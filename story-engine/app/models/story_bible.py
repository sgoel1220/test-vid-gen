"""Story bible models — narrator, setting, horror rules, subplots."""

from __future__ import annotations

from typing import List

from creepy_pasta_protocol.common import Frozen


class NarratorProfile(Frozen):
    name: str
    age_range: str
    occupation: str
    personality_traits: List[str]
    speech_patterns: str
    reason_for_recounting: str


class SettingDetail(Frozen):
    location: str
    time_period: str
    atmosphere: str
    key_locations: List[str]
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
    subplots: List[Subplot]
    foreshadowing_seeds: List[ForeshadowingSeed]
    thematic_core: str

"""Act draft and inline check models."""

from __future__ import annotations

from typing import List

from creepy_pasta_protocol.common import Frozen


class ActDraft(Frozen):
    act_number: int
    title: str
    text: str
    word_count: int


class ActInlineCheck(Frozen):
    act_number: int
    beats_matched: bool
    voice_consistent: bool
    contradictions: List[str]
    pacing_ok: bool
    passes: bool
    notes: str

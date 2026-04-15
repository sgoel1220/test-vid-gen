"""Chunk validation snapshot DTO."""

from __future__ import annotations

from typing import List

from .common import Frozen


class ChunkValidationSnapshot(Frozen):
    passed: bool
    duration_sec: float
    rms_energy: float
    peak_amplitude: float
    voiced_ratio: float
    failures: List[str]

"""Frozen snapshot of a resolved TTS run's settings."""

from __future__ import annotations

from typing import List, Optional

from pydantic import Field

from .common import AudioFormat, Frozen


class ResolvedSettingsSnapshot(Frozen):
    reference_audio_filename: str
    output_format: AudioFormat
    target_sample_rate: int
    split_text: bool
    chunk_size: int
    temperature: float
    exaggeration: float
    cfg_weight: float
    seed: int
    speed_factor: float
    language: str
    enable_smart_stitching: bool
    sentence_pause_ms: int
    crossfade_ms: int
    safety_fade_ms: int
    enable_dc_removal: bool
    dc_highpass_hz: int
    peak_normalize_threshold: float
    peak_normalize_target: float
    enable_silence_trimming: bool
    enable_internal_silence_fix: bool
    enable_unvoiced_removal: bool
    max_reference_duration_sec: Optional[int] = None
    save_chunk_audio: bool
    save_final_audio: bool
    run_label: Optional[str] = None
    enable_chunk_validation: bool
    max_chunk_retries: int
    chunk_validation_min_rms: float
    chunk_validation_min_peak: float
    chunk_validation_min_voiced_ratio: float
    enable_text_normalization: bool
    text_normalization_model_id: str
    selected_chunk_indices: List[int] = Field(default_factory=list)

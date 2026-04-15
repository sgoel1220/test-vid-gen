"""Backward-compatibility shim — re-exports from the new focused modules.

All functionality has been split into:
  audio/   — encoding, processing, stitching
  text/    — chunking, normalisation
  files.py — reference-audio helpers, PerformanceMonitor
  models.py — Pydantic data models
"""

from audio.encoding import encode_audio, save_audio_to_file, save_audio_tensor_to_file
from audio.processing import (
    LIBROSA_AVAILABLE,
    PARSELMOUTH_AVAILABLE,
    apply_speed_factor,
    fix_internal_silence,
    remove_long_unvoiced_segments,
    trim_lead_trail_silence,
    validate_chunk_audio,
)
from audio.stitching import post_process_final_audio, stitch_audio_chunks
from files import (
    PerformanceMonitor,
    get_predefined_voices,
    get_valid_reference_files,
    validate_reference_audio,
)
from models import ChunkValidationResult, SavedAudioArtifact
from text import chunk_text_by_sentences, normalize_text_with_llm, sanitize_filename

__all__ = [
    # audio.encoding
    "encode_audio",
    "save_audio_to_file",
    "save_audio_tensor_to_file",
    # audio.processing
    "LIBROSA_AVAILABLE",
    "PARSELMOUTH_AVAILABLE",
    "apply_speed_factor",
    "fix_internal_silence",
    "remove_long_unvoiced_segments",
    "trim_lead_trail_silence",
    "validate_chunk_audio",
    # audio.stitching
    "post_process_final_audio",
    "stitch_audio_chunks",
    # files
    "PerformanceMonitor",
    "get_predefined_voices",
    "get_valid_reference_files",
    "validate_reference_audio",
    # models
    "ChunkValidationResult",
    "SavedAudioArtifact",
    # text
    "chunk_text_by_sentences",
    "normalize_text_with_llm",
    "sanitize_filename",
]

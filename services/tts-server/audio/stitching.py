"""Audio chunk stitching and post-processing pipeline."""

from __future__ import annotations

import logging
from typing import List

import numpy as np

from audio.processing import (
    PARSELMOUTH_AVAILABLE,
    fix_internal_silence,
    remove_long_unvoiced_segments,
    trim_lead_trail_silence,
)
from models import ResolvedSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Crossfade helpers
# ---------------------------------------------------------------------------

def _equal_power_curves(n: int) -> tuple[np.ndarray, np.ndarray]:
    t = np.linspace(0, np.pi / 2, n, dtype=np.float32)
    return np.cos(t) ** 2, np.sin(t) ** 2


def _crossfade_overlap(a: np.ndarray, b: np.ndarray, fade: int) -> np.ndarray:
    fade = min(fade, len(a), len(b))
    if fade <= 0:
        return np.concatenate([a, b])
    fo, fi = _equal_power_curves(fade)
    region = a[-fade:] * fo + b[:fade] * fi
    return np.concatenate([a[:-fade], region, b[fade:]])


def _edge_fades(
    chunk: np.ndarray, fade: int, fade_in: bool = True, fade_out: bool = True
) -> np.ndarray:
    if len(chunk) < fade * 2:
        return chunk.astype(np.float32, copy=False)
    result = chunk.astype(np.float32, copy=True)
    if fade_in:
        result[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
    if fade_out:
        result[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return result


def _remove_dc(audio: np.ndarray, sample_rate: int, cutoff_hz: float = 15.0) -> np.ndarray:
    try:
        from scipy.signal import butter, filtfilt
        b, a = butter(2, cutoff_hz / (sample_rate / 2), btype="high")
        return filtfilt(b, a, audio).astype(np.float32)
    except ImportError:
        logger.warning("scipy unavailable — DC offset removal skipped.")
        return audio.astype(np.float32, copy=False)
    except Exception as exc:
        logger.error("DC offset removal failed: %s", exc)
        return audio.astype(np.float32, copy=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def stitch_audio_chunks(
    audio_chunks: List[np.ndarray],
    sample_rate: int,
    settings: ResolvedSettings,
) -> np.ndarray:
    """Stitch synthesised audio chunks into a single array."""
    if not audio_chunks:
        raise ValueError("No audio chunks provided for stitching.")
    if len(audio_chunks) == 1:
        return audio_chunks[0].astype(np.float32, copy=False)

    if settings.enable_smart_stitching:
        fade = int(settings.crossfade_ms / 1000 * sample_rate)
        silence_buf = int(settings.sentence_pause_ms / 1000 * sample_rate) + fade * 2

        processed = []
        for chunk in audio_chunks:
            c = chunk.astype(np.float32, copy=True)
            if settings.enable_dc_removal:
                c = _remove_dc(c, sample_rate, settings.dc_highpass_hz)
            processed.append(c)

        result = processed[0]
        for nxt in processed[1:]:
            silence = np.zeros(silence_buf, dtype=np.float32)
            result = _crossfade_overlap(result, silence, fade)
            result = _crossfade_overlap(result, nxt, fade)
        return result.astype(np.float32, copy=False)

    # Fallback: simple edge fades
    fade = int(settings.safety_fade_ms / 1000 * sample_rate)
    last = len(audio_chunks) - 1
    return np.concatenate([
        _edge_fades(c, fade, fade_in=i != 0, fade_out=i != last)
        for i, c in enumerate(audio_chunks)
    ]).astype(np.float32, copy=False)


def post_process_final_audio(
    audio_array: np.ndarray,
    sample_rate: int,
    settings: ResolvedSettings,
    warnings: List[str],
) -> np.ndarray:
    """Apply normalisation, silence trimming, and unvoiced removal."""
    audio = audio_array.astype(np.float32, copy=False)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > settings.peak_normalize_threshold:
        audio = audio * (settings.peak_normalize_target / peak)
        warnings.append(f"Final audio normalised to prevent clipping (peak was {peak:.3f}).")

    if settings.enable_silence_trimming:
        audio = trim_lead_trail_silence(audio, sample_rate)

    if settings.enable_internal_silence_fix:
        audio = fix_internal_silence(audio, sample_rate)

    if settings.enable_unvoiced_removal:
        if PARSELMOUTH_AVAILABLE:
            audio = remove_long_unvoiced_segments(audio, sample_rate)
        else:
            warnings.append("enable_unvoiced_removal requested but Parselmouth is not available.")

    return audio.astype(np.float32, copy=False)

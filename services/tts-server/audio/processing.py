"""Post-generation audio transforms, speed adjustment, and chunk validation."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
import torch
import torchaudio

from models import ChunkValidationResult

logger = logging.getLogger(__name__)

try:
    import librosa
    LIBROSA_AVAILABLE = True
    logger.info("Librosa found — advanced audio processing enabled.")
except ImportError:
    librosa = None  # type: ignore[assignment]
    LIBROSA_AVAILABLE = False
    logger.warning("Librosa not found. Speed adjustment and silence trimming will be limited.")

try:
    import parselmouth
    PARSELMOUTH_AVAILABLE = True
    logger.info("Parselmouth found — unvoiced segment removal enabled.")
except ImportError:
    parselmouth = None  # type: ignore[assignment]
    PARSELMOUTH_AVAILABLE = False
    logger.warning("Parselmouth not found. Unvoiced segment removal disabled.")


def apply_speed_factor(
    audio_tensor: torch.Tensor, sample_rate: int, speed_factor: float
) -> Tuple[torch.Tensor, int]:
    """Apply a speed factor using librosa time-stretch (pitch-preserving)."""
    if speed_factor == 1.0:
        return audio_tensor, sample_rate
    if speed_factor <= 0:
        logger.warning("Invalid speed_factor %s — must be positive.", speed_factor)
        return audio_tensor, sample_rate

    cpu = audio_tensor.cpu()
    if cpu.ndim == 2:
        cpu = cpu.squeeze(0) if cpu.shape[0] == 1 else (
            cpu.squeeze(1) if cpu.shape[1] == 1 else cpu[0, :]
        )
    if cpu.ndim != 1:
        logger.error("apply_speed_factor: unexpected tensor shape %s.", cpu.shape)
        return audio_tensor, sample_rate

    if LIBROSA_AVAILABLE:
        try:
            stretched = librosa.effects.time_stretch(y=cpu.numpy(), rate=speed_factor)
            logger.info("Applied speed factor %.2f via librosa.", speed_factor)
            return torch.from_numpy(stretched), sample_rate
        except Exception as exc:
            logger.error("librosa time_stretch failed: %s. Returning original.", exc)
    else:
        logger.warning("Librosa unavailable for speed adjustment. Returning original.")
    return audio_tensor, sample_rate


def trim_lead_trail_silence(
    audio_array: np.ndarray,
    sample_rate: int,
    silence_threshold_db: float = -40.0,
    min_silence_duration_ms: int = 100,
    padding_ms: int = 50,
) -> np.ndarray:
    """Trim leading/trailing silence using librosa."""
    if audio_array is None or audio_array.size == 0:
        return audio_array
    if not LIBROSA_AVAILABLE:
        logger.warning("Librosa unavailable — skipping silence trimming.")
        return audio_array
    try:
        trimmed, index = librosa.effects.trim(
            y=audio_array,
            top_db=abs(silence_threshold_db),
            frame_length=2048,
            hop_length=512,
        )
        start, end = index[0], index[1]
        pad = int(padding_ms / 1000.0 * sample_rate)
        final_start = max(0, start - pad)
        final_end = min(len(audio_array), end + pad)
        if (index[0] > 0 or index[1] < len(audio_array)) and final_end > final_start:
            return audio_array[final_start:final_end]
        return audio_array
    except Exception as exc:
        logger.error("Silence trimming failed: %s", exc, exc_info=True)
        return audio_array


def fix_internal_silence(
    audio_array: np.ndarray,
    sample_rate: int,
    silence_threshold_db: float = -40.0,
    min_silence_to_fix_ms: int = 700,
    max_allowed_silence_ms: int = 300,
) -> np.ndarray:
    """Shorten long internal silences using librosa."""
    if audio_array is None or audio_array.size == 0:
        return audio_array
    if not LIBROSA_AVAILABLE:
        logger.warning("Librosa unavailable — skipping internal silence fix.")
        return audio_array
    try:
        min_samples = int(min_silence_to_fix_ms / 1000.0 * sample_rate)
        max_keep = int(max_allowed_silence_ms / 1000.0 * sample_rate)
        intervals = librosa.effects.split(
            y=audio_array, top_db=abs(silence_threshold_db),
            frame_length=2048, hop_length=512,
        )
        if len(intervals) <= 1:
            return audio_array

        parts: list[np.ndarray] = []
        last_end = 0
        for start, end in intervals:
            silence_len = start - last_end
            if silence_len > 0:
                parts.append(
                    audio_array[last_end : last_end + max_keep]
                    if silence_len >= min_samples
                    else audio_array[last_end:start]
                )
            parts.append(audio_array[start:end])
            last_end = end

        trailing = len(audio_array) - last_end
        if trailing > 0:
            parts.append(
                audio_array[last_end : last_end + max_keep]
                if trailing >= min_samples
                else audio_array[last_end:]
            )

        return np.concatenate(parts) if parts else audio_array
    except Exception as exc:
        logger.error("Internal silence fix failed: %s", exc, exc_info=True)
        return audio_array


def remove_long_unvoiced_segments(
    audio_array: np.ndarray,
    sample_rate: int,
    min_unvoiced_duration_ms: int = 300,
    pitch_floor: float = 75.0,
    pitch_ceiling: float = 600.0,
) -> np.ndarray:
    """Remove long unvoiced segments using Parselmouth pitch analysis."""
    if not PARSELMOUTH_AVAILABLE:
        logger.warning("Parselmouth unavailable — skipping unvoiced segment removal.")
        return audio_array
    if audio_array is None or audio_array.size == 0:
        return audio_array
    try:
        sound = parselmouth.Sound(audio_array.astype(np.float64), sampling_frequency=sample_rate)
        pitch = sound.to_pitch(pitch_floor=pitch_floor, pitch_ceiling=pitch_ceiling)
        vvu = pitch.get_VoicedVoicelessUnvoiced()
        min_samples = int(min_unvoiced_duration_ms / 1000.0 * sample_rate)

        keep: list[np.ndarray] = []
        cur = 0
        for t_start, t_end, label in vvu.time_intervals:
            s = int(t_start * sample_rate)
            e = int(t_end * sample_rate)
            dur = e - s
            if label == "voiced":
                keep.append(audio_array[cur:e])
                cur = e
            elif dur < min_samples:
                keep.append(audio_array[cur:e])
                cur = e
            else:
                if s > cur:
                    keep.append(audio_array[cur:s])
                cur = e

        if cur < len(audio_array):
            keep.append(audio_array[cur:])

        if not keep:
            logger.warning("Unvoiced removal left no audio — returning original.")
            return audio_array
        return np.concatenate(keep)
    except Exception as exc:
        logger.error("Unvoiced segment removal failed: %s", exc, exc_info=True)
        return audio_array


# ---------------------------------------------------------------------------
# Chunk audio validation
# ---------------------------------------------------------------------------

def validate_chunk_audio(
    audio_array: np.ndarray,
    sample_rate: int,
    *,
    min_duration_sec: float = 0.1,
    max_duration_sec: float = 60.0,
    min_rms_energy: float = 1e-4,
    min_peak_amplitude: float = 1e-3,
    min_voiced_ratio: float = 0.05,
) -> ChunkValidationResult:
    """Validate a synthesised audio chunk; returns a typed result."""
    failures: List[str] = []

    if audio_array is None or audio_array.size == 0:
        failures.append("empty")
        return ChunkValidationResult(
            passed=False, duration_sec=0.0, rms_energy=0.0,
            peak_amplitude=0.0, voiced_ratio=0.0, failures=failures,
        )

    arr = audio_array.astype(np.float32, copy=False)
    duration_sec = round(float(len(arr)) / float(sample_rate), 4)
    if duration_sec < min_duration_sec:
        failures.append("duration_too_short")
    if duration_sec > max_duration_sec:
        failures.append("duration_too_long")

    rms = round(float(np.sqrt(np.mean(arr**2))), 8)
    if rms < min_rms_energy:
        failures.append("silent_rms")

    peak = round(float(np.abs(arr).max()), 8)
    if peak < min_peak_amplitude:
        failures.append("silent_peak")

    frame_len = min(512, len(arr))
    hop = frame_len // 2 or 1
    threshold = min_rms_energy * 10.0
    n_frames = max(1, (len(arr) - frame_len) // hop + 1)
    active = sum(
        1 for i in range(n_frames)
        if float(np.sqrt(np.mean(arr[i * hop : i * hop + frame_len] ** 2))) >= threshold
    )
    voiced_ratio = round(float(active) / float(n_frames), 4)
    if voiced_ratio < min_voiced_ratio:
        failures.append("low_voiced_ratio")

    return ChunkValidationResult(
        passed=len(failures) == 0,
        duration_sec=duration_sec,
        rms_energy=rms,
        peak_amplitude=peak,
        voiced_ratio=voiced_ratio,
        failures=failures,
    )

"""Audio validation for TTS chunk output.

Validates WAV bytes returned by the GPU TTS pod using numpy signal metrics.
Validation runs in creepy-brain (orchestrator), not on the GPU pod.
"""

from __future__ import annotations

import io
import logging

import numpy as np
import soundfile as sf
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# Thresholds — tuned for Chatterbox speech output
_MIN_RMS: float = 0.003          # silence floor
_MIN_PEAK: float = 0.02          # minimum peak amplitude
_MIN_VOICED_RATIO: float = 0.03  # at least 3% voiced frames
_VOICED_THRESHOLD: float = 0.01  # frame RMS above this = voiced


class ChunkValidationResult(BaseModel):
    """Result of audio chunk validation."""

    passed: bool = Field(..., description="True if all metrics are within acceptable range")
    rms: float = Field(..., description="Root mean square amplitude (0–1)")
    peak_amplitude: float = Field(..., description="Peak sample amplitude (0–1)")
    voiced_ratio: float = Field(..., description="Fraction of frames with RMS above voiced threshold")
    duration_sec: float = Field(..., description="Audio duration in seconds")
    failure_reason: str = Field(default="", description="Human-readable reason if not passed")


def validate_chunk_audio(wav_bytes: bytes) -> ChunkValidationResult:
    """Validate WAV bytes returned by the TTS GPU pod.

    Computes RMS, peak amplitude, and voiced frame ratio from the audio
    signal. Returns a :class:`ChunkValidationResult` indicating whether
    the chunk is acceptable for use in the final mix.

    Args:
        wav_bytes: Raw WAV bytes from the ``/synthesize`` endpoint.

    Returns:
        Validation result with metrics and a ``passed`` flag.
    """
    try:
        audio: np.ndarray[tuple[int, ...], np.dtype[np.float32]]
        audio, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    except Exception as exc:
        log.warning("failed to decode WAV bytes: %s", exc)
        return ChunkValidationResult(
            passed=False,
            rms=0.0,
            peak_amplitude=0.0,
            voiced_ratio=0.0,
            duration_sec=0.0,
            failure_reason=f"decode error: {exc}",
        )

    # Flatten to mono for metrics
    mono: np.ndarray[tuple[int, ...], np.dtype[np.float32]]
    if audio.ndim > 1:
        mono = audio.mean(axis=1)
    else:
        mono = audio

    n_samples = len(mono)
    duration_sec = n_samples / sample_rate if sample_rate > 0 else 0.0

    if n_samples == 0:
        return ChunkValidationResult(
            passed=False,
            rms=0.0,
            peak_amplitude=0.0,
            voiced_ratio=0.0,
            duration_sec=0.0,
            failure_reason="empty audio",
        )

    rms = float(np.sqrt(np.mean(mono**2)))
    peak_amplitude = float(np.max(np.abs(mono)))

    # Voiced ratio: fraction of 20ms frames with RMS above threshold
    frame_size = max(1, int(sample_rate * 0.02))
    n_frames = n_samples // frame_size
    voiced_frames = 0
    for i in range(n_frames):
        frame = mono[i * frame_size : (i + 1) * frame_size]
        frame_rms = float(np.sqrt(np.mean(frame**2)))
        if frame_rms > _VOICED_THRESHOLD:
            voiced_frames += 1
    voiced_ratio = voiced_frames / n_frames if n_frames > 0 else 0.0

    failures: list[str] = []
    if rms < _MIN_RMS:
        failures.append(f"rms={rms:.4f} < {_MIN_RMS}")
    if peak_amplitude < _MIN_PEAK:
        failures.append(f"peak={peak_amplitude:.4f} < {_MIN_PEAK}")
    if voiced_ratio < _MIN_VOICED_RATIO:
        failures.append(f"voiced_ratio={voiced_ratio:.2%} < {_MIN_VOICED_RATIO:.0%}")

    passed = len(failures) == 0
    result = ChunkValidationResult(
        passed=passed,
        rms=rms,
        peak_amplitude=peak_amplitude,
        voiced_ratio=voiced_ratio,
        duration_sec=duration_sec,
        failure_reason="; ".join(failures),
    )

    if passed:
        log.debug(
            "chunk validation passed rms=%.4f peak=%.4f voiced=%.1f%% dur=%.1fs",
            rms, peak_amplitude, voiced_ratio * 100, duration_sec,
        )
    else:
        log.warning(
            "chunk validation failed: %s (rms=%.4f peak=%.4f voiced=%.1f%%)",
            result.failure_reason, rms, peak_amplitude, voiced_ratio * 100,
        )

    return result

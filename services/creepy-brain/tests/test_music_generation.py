"""Unit tests for music_generation pipeline step (bead evk).

Tests cover the pure-function audio helpers that have no DB/GPU dependencies:
- _scene_duration
- _validate_wav_response
- _extract_tail_b64
- _crossfade_and_concat
"""

from __future__ import annotations

import io

import numpy as np
import pytest
import soundfile as sf
from unittest.mock import MagicMock

from app.workflows.steps.music_generation import (
    _crossfade_and_concat,
    _extract_tail_b64,
    _scene_duration,
    _validate_wav_response,
)
from app.text.scene_grouping import Scene


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav(duration_sec: float, sample_rate: int = 44100, channels: int = 1) -> bytes:
    """Generate a silent WAV of the given duration."""
    n_samples = int(duration_sec * sample_rate)
    data = np.zeros((n_samples, channels) if channels > 1 else n_samples, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, data, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _make_scene(scene_index: int, chunk_indices: list[int]) -> Scene:
    combined_text = " ".join(f"chunk {i}" for i in chunk_indices)
    return Scene(scene_index=scene_index, chunk_indices=chunk_indices, combined_text=combined_text)


# ---------------------------------------------------------------------------
# _scene_duration
# ---------------------------------------------------------------------------


class TestSceneDuration:
    def test_sums_chunk_durations(self) -> None:
        scene = _make_scene(0, [0, 1, 2])
        durations: dict[int, float | None] = {0: 5.0, 1: 3.0, 2: 2.0}
        assert _scene_duration(scene, durations) == pytest.approx(10.0)

    def test_none_durations_treated_as_zero(self) -> None:
        scene = _make_scene(0, [0, 1])
        durations: dict[int, float | None] = {0: None, 1: None}
        # Falls back to default
        result = _scene_duration(scene, durations)
        assert result == pytest.approx(30.0)  # _DEFAULT_SCENE_DURATION_SEC

    def test_missing_indices_treated_as_zero(self) -> None:
        scene = _make_scene(0, [5, 6])
        durations: dict[int, float | None] = {}
        result = _scene_duration(scene, durations)
        assert result == pytest.approx(30.0)  # fallback

    def test_partial_none_sums_non_none(self) -> None:
        scene = _make_scene(0, [0, 1])
        durations: dict[int, float | None] = {0: 7.5, 1: None}
        result = _scene_duration(scene, durations)
        assert result == pytest.approx(7.5)


# ---------------------------------------------------------------------------
# _validate_wav_response
# ---------------------------------------------------------------------------


class TestValidateWavResponse:
    def _make_resp(self, content_type: str, content: bytes) -> MagicMock:
        resp = MagicMock()
        resp.headers = {"content-type": content_type}
        resp.content = content
        return resp

    def test_accepts_audio_wav(self) -> None:
        wav = _make_wav(1.0)
        resp = self._make_resp("audio/wav", wav)
        result = _validate_wav_response(resp)
        assert result == wav

    def test_accepts_audio_x_wav(self) -> None:
        wav = _make_wav(0.5)
        resp = self._make_resp("audio/x-wav", wav)
        result = _validate_wav_response(resp)
        assert result == wav

    def test_rejects_wrong_content_type(self) -> None:
        resp = self._make_resp("application/json", b"{}")
        with pytest.raises(ValueError, match="Expected audio/wav"):
            _validate_wav_response(resp)

    def test_rejects_empty_content(self) -> None:
        resp = self._make_resp("audio/wav", b"")
        with pytest.raises(ValueError, match="Empty response"):
            _validate_wav_response(resp)

    def test_rejects_non_riff_bytes(self) -> None:
        resp = self._make_resp("audio/wav", b"NOTAWAVE")
        with pytest.raises(ValueError, match="RIFF"):
            _validate_wav_response(resp)


# ---------------------------------------------------------------------------
# _extract_tail_b64
# ---------------------------------------------------------------------------


class TestExtractTailB64:
    def test_returns_base64_string(self) -> None:
        wav = _make_wav(10.0)
        result = _extract_tail_b64(wav, 3.0)
        assert isinstance(result, str)
        # Should be valid base64
        import base64
        decoded = base64.b64decode(result)
        assert decoded.startswith(b"RIFF")

    def test_short_audio_returns_entire_clip(self) -> None:
        """When audio is shorter than tail_sec, return all of it."""
        wav = _make_wav(1.0, sample_rate=44100)
        result = _extract_tail_b64(wav, 5.0)  # 5s tail from 1s clip
        import base64
        decoded = base64.b64decode(result)
        data, sr = sf.read(io.BytesIO(decoded))
        # Should be ≤ 1.0 seconds (the full clip)
        assert len(data) / sr <= 1.1

    def test_tail_duration_approximately_correct(self) -> None:
        wav = _make_wav(20.0, sample_rate=16000)
        tail_sec = 5.0
        result = _extract_tail_b64(wav, tail_sec)
        import base64
        decoded = base64.b64decode(result)
        data, sr = sf.read(io.BytesIO(decoded))
        actual_sec = len(data) / sr
        assert abs(actual_sec - tail_sec) < 0.1


# ---------------------------------------------------------------------------
# _crossfade_and_concat
# ---------------------------------------------------------------------------


class TestCrossfadeAndConcat:
    def test_single_segment_passthrough(self) -> None:
        wav = _make_wav(5.0, sample_rate=16000)
        result = _crossfade_and_concat([wav])
        data, sr = sf.read(io.BytesIO(result))
        assert sr == 16000
        assert abs(len(data) / sr - 5.0) < 0.1

    def test_two_segments_combined(self) -> None:
        seg1 = _make_wav(4.0, sample_rate=16000)
        seg2 = _make_wav(4.0, sample_rate=16000)
        result = _crossfade_and_concat([seg1, seg2], crossfade_sec=0.5)
        data, sr = sf.read(io.BytesIO(result))
        # Combined should be slightly less than 8s due to crossfade overlap
        duration = len(data) / sr
        assert 7.0 < duration < 8.0

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="No segments"):
            _crossfade_and_concat([])

    def test_stereo_segments_handled(self) -> None:
        seg1 = _make_wav(3.0, sample_rate=22050, channels=2)
        seg2 = _make_wav(3.0, sample_rate=22050, channels=2)
        result = _crossfade_and_concat([seg1, seg2], crossfade_sec=0.5)
        data, sr = sf.read(io.BytesIO(result))
        assert data.ndim == 2
        assert data.shape[1] == 2

    def test_three_segments_combined(self) -> None:
        segments = [_make_wav(5.0, sample_rate=16000) for _ in range(3)]
        result = _crossfade_and_concat(segments, crossfade_sec=0.5)
        data, sr = sf.read(io.BytesIO(result))
        duration = len(data) / sr
        # 3 × 5s with 2 crossfades of 0.5s each = ~14s
        assert 13.0 < duration < 15.5

    def test_output_is_valid_wav(self) -> None:
        segs = [_make_wav(2.0, sample_rate=44100) for _ in range(2)]
        result = _crossfade_and_concat(segs)
        assert result.startswith(b"RIFF")

    def test_many_segments_incremental(self) -> None:
        """12 segments — verifies incremental path handles >10 segments correctly."""
        n = 12
        seg_dur = 3.0
        crossfade = 0.5
        segments = [_make_wav(seg_dur, sample_rate=16000) for _ in range(n)]
        result = _crossfade_and_concat(segments, crossfade_sec=crossfade)
        data, sr = sf.read(io.BytesIO(result))
        assert result.startswith(b"RIFF")
        expected_min = n * seg_dur - (n - 1) * crossfade - 0.5
        expected_max = n * seg_dur + 0.5
        duration = len(data) / sr
        assert expected_min < duration < expected_max

    def test_short_middle_segment_crossfades_correctly(self) -> None:
        """Middle segments shorter than 2*crossfade_sec must not break adjacent crossfades.

        Previously, pending = nxt[actual_fade:] left too few samples for the
        next iteration's crossfade when a segment was short.  The fixed
        invariant keeps the last crossfade_samples of accumulated output in
        pending so both adjacent crossfades use a full window.
        """
        crossfade = 0.5
        sr = 16000
        long1 = _make_wav(4.0, sample_rate=sr)   # normal
        short = _make_wav(0.6, sample_rate=sr)   # shorter than 2 * crossfade_sec
        long2 = _make_wav(4.0, sample_rate=sr)   # normal
        result = _crossfade_and_concat([long1, short, long2], crossfade_sec=crossfade)
        data, _ = sf.read(io.BytesIO(result))
        assert result.startswith(b"RIFF")
        # Total = 4 + 0.6 + 4 - 2*0.5 crossfades = 7.6s; allow ±0.5s tolerance
        duration = len(data) / sr
        assert 7.0 < duration < 8.5

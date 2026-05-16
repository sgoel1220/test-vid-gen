"""Unit tests for ellipsis-pause TTS helpers.

Tests cover:
- _stitch_wav_with_silence: silence insertion between WAV pieces
- _synthesize_chunk_with_pauses: split/stitch orchestration and pass-through
"""

from __future__ import annotations

import io
import struct
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

from app.workflows.steps.tts import (
    ChunkSynthesisResult,
    _MAX_ELLIPSIS_PIECES,
    _stitch_wav_with_silence,
    _synthesize_chunk_with_pauses,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_SAMPLE_RATE = 24000


def _make_wav(duration_sec: float = 0.1, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Return minimal valid PCM_16 WAV bytes for the given duration."""
    n_samples = int(duration_sec * sample_rate)
    audio = (np.random.rand(n_samples).astype(np.float32) * 0.1)
    audio_int16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    sf.write(buf, audio_int16, sample_rate, format="wav", subtype="PCM_16")
    return buf.getvalue()


def _read_wav_duration(wav_bytes: bytes) -> float:
    """Read WAV and return duration in seconds."""
    arr, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    return len(arr) / sr


def _make_chunk_result(
    text: str = "hello",
    duration_sec: float = 0.1,
    validation_passed: bool = True,
    attempts_used: int = 1,
) -> ChunkSynthesisResult:
    return ChunkSynthesisResult(
        wav_bytes=_make_wav(duration_sec),
        attempts_used=attempts_used,
        duration_sec=duration_sec,
        validation_passed=validation_passed,
    )


# ---------------------------------------------------------------------------
# _stitch_wav_with_silence
# ---------------------------------------------------------------------------


class TestStitchWavWithSilence:
    def test_single_piece_returned_unchanged(self) -> None:
        wav = _make_wav(0.2)
        result = _stitch_wav_with_silence([wav], pause_sec=0.5)
        assert result == wav

    def test_two_pieces_duration_includes_silence(self) -> None:
        piece_dur = 0.1
        pause_sec = 0.3
        pieces = [_make_wav(piece_dur), _make_wav(piece_dur)]
        result = _stitch_wav_with_silence(pieces, pause_sec=pause_sec)
        actual_dur = _read_wav_duration(result)
        expected = piece_dur * 2 + pause_sec
        assert abs(actual_dur - expected) < 0.01

    def test_three_pieces_have_two_silences(self) -> None:
        piece_dur = 0.05
        pause_sec = 0.2
        pieces = [_make_wav(piece_dur) for _ in range(3)]
        result = _stitch_wav_with_silence(pieces, pause_sec=pause_sec)
        actual_dur = _read_wav_duration(result)
        expected = piece_dur * 3 + pause_sec * 2
        assert abs(actual_dur - expected) < 0.01

    def test_result_is_valid_wav(self) -> None:
        pieces = [_make_wav(0.1), _make_wav(0.1)]
        result = _stitch_wav_with_silence(pieces, pause_sec=0.1)
        # sf.read must not raise
        arr, sr = sf.read(io.BytesIO(result), dtype="float32")
        assert sr == _SAMPLE_RATE
        assert len(arr) > 0

    def test_zero_pause_no_silence_added(self) -> None:
        piece_dur = 0.1
        pieces = [_make_wav(piece_dur), _make_wav(piece_dur)]
        result = _stitch_wav_with_silence(pieces, pause_sec=0.0)
        actual_dur = _read_wav_duration(result)
        assert abs(actual_dur - piece_dur * 2) < 0.01


# ---------------------------------------------------------------------------
# _synthesize_chunk_with_pauses
# ---------------------------------------------------------------------------


def _mock_retry(result: ChunkSynthesisResult) -> AsyncMock:
    """Return an AsyncMock that patches _synthesize_with_retry."""
    return AsyncMock(return_value=result)


class TestSynthesizeChunkWithPauses:
    @pytest.mark.asyncio
    async def test_no_ellipsis_delegates_directly(self) -> None:
        """Text without ... calls _synthesize_with_retry once unchanged."""
        expected = _make_chunk_result()
        client = MagicMock()

        with patch(
            "app.workflows.steps.tts._synthesize_with_retry",
            new=AsyncMock(return_value=expected),
        ) as mock_retry:
            result = await _synthesize_chunk_with_pauses(
                client=client,
                chunk_text="hello world",
                chunk_index=0,
                voice_name="v.wav",
                max_retries=1,
            )

        mock_retry.assert_awaited_once()
        call_kwargs = mock_retry.call_args.kwargs
        assert call_kwargs["chunk_text"] == "hello world"
        assert result is expected

    @pytest.mark.asyncio
    async def test_single_ellipsis_splits_into_two_pieces(self) -> None:
        """'hello... world' → two synthesis calls + silence."""
        results = [_make_chunk_result(duration_sec=0.1), _make_chunk_result(duration_sec=0.1)]
        call_count = 0

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            return r

        pause_sec = 0.3

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.settings") as mock_settings:
                mock_settings.tts_ellipsis_pause_sec = pause_sec
                mock_settings.tts_exaggeration = 0.5
                mock_settings.tts_cfg_weight = 0.5
                mock_settings.tts_temperature = 0.8
                mock_settings.tts_repetition_penalty = 1.2
                mock_settings.tts_min_p = 0.05
                mock_settings.tts_top_p = 1.0
                mock_settings.tts_seed = 0
                result = await _synthesize_chunk_with_pauses(
                    client=MagicMock(),
                    chunk_text="hello... world",
                    chunk_index=0,
                    voice_name="v.wav",
                    max_retries=1,
                )

        assert call_count == 2
        # duration = 0.1 + 0.3 + 0.1 = 0.5
        assert abs(result.duration_sec - 0.5) < 0.01
        assert result.validation_passed is True

    @pytest.mark.asyncio
    async def test_three_ellipses_produce_four_pieces(self) -> None:
        """'a... b... c... d' → 4 calls."""
        call_count = 0

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            nonlocal call_count
            call_count += 1
            return _make_chunk_result(duration_sec=0.05)

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.settings") as mock_settings:
                mock_settings.tts_ellipsis_pause_sec = 0.1
                mock_settings.tts_exaggeration = 0.5
                mock_settings.tts_cfg_weight = 0.5
                mock_settings.tts_temperature = 0.8
                mock_settings.tts_repetition_penalty = 1.2
                mock_settings.tts_min_p = 0.05
                mock_settings.tts_top_p = 1.0
                mock_settings.tts_seed = 0
                result = await _synthesize_chunk_with_pauses(
                    client=MagicMock(),
                    chunk_text="a... b... c... d",
                    chunk_index=0,
                    voice_name="v.wav",
                    max_retries=0,
                )

        assert call_count == 4
        # 4 * 0.05 audio + 3 * 0.1 silence = 0.2 + 0.3 = 0.5
        assert abs(result.duration_sec - 0.5) < 0.01

    @pytest.mark.asyncio
    async def test_failed_sub_piece_marks_result_not_validated(self) -> None:
        """If validate_chunk_audio says the stitch failed, validation_passed is False."""
        from app.audio.validation import ChunkValidationResult

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            return _make_chunk_result(validation_passed=True)

        failing = ChunkValidationResult(
            passed=False,
            rms=0.01,
            peak_amplitude=0.1,
            voiced_ratio=0.1,
            duration_sec=0.3,
            failure_reason="voiced_ratio 10% < 30%",
        )

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.validate_chunk_audio", return_value=failing):
                with patch("app.workflows.steps.tts.settings") as mock_settings:
                    mock_settings.tts_ellipsis_pause_sec = 0.1
                    mock_settings.tts_exaggeration = 0.5
                    mock_settings.tts_cfg_weight = 0.5
                    mock_settings.tts_temperature = 0.8
                    mock_settings.tts_repetition_penalty = 1.2
                    mock_settings.tts_min_p = 0.05
                    mock_settings.tts_top_p = 1.0
                    mock_settings.tts_seed = 0
                    result = await _synthesize_chunk_with_pauses(
                        client=MagicMock(),
                        chunk_text="that... failed",
                        chunk_index=2,
                        voice_name="v.wav",
                        max_retries=0,
                    )

        assert result.validation_passed is False

    @pytest.mark.asyncio
    async def test_whitespace_only_pieces_are_skipped(self) -> None:
        """Leading/trailing ... produce whitespace-only pieces that are dropped."""
        call_count = 0

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            nonlocal call_count
            call_count += 1
            return _make_chunk_result()

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.settings") as mock_settings:
                mock_settings.tts_ellipsis_pause_sec = 0.1
                mock_settings.tts_exaggeration = 0.5
                mock_settings.tts_cfg_weight = 0.5
                mock_settings.tts_temperature = 0.8
                mock_settings.tts_repetition_penalty = 1.2
                mock_settings.tts_min_p = 0.05
                mock_settings.tts_top_p = 1.0
                mock_settings.tts_seed = 0
                # "...hello..." → pieces after strip/filter = ["hello"]
                # Only 1 real piece → falls through to direct call
                await _synthesize_chunk_with_pauses(
                    client=MagicMock(),
                    chunk_text="...hello...",
                    chunk_index=0,
                    voice_name="v.wav",
                    max_retries=0,
                )

        # Only "hello" remains → single piece → 1 call via pass-through
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_stitched_wav_is_revalidated(self) -> None:
        """validation_passed on the result reflects validate_chunk_audio of the stitched WAV."""
        sub_result = _make_chunk_result(validation_passed=True)

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            return sub_result

        # Patch validate_chunk_audio to say the stitch failed (voiced_ratio too low)
        from app.audio.validation import ChunkValidationResult

        failing_validation = ChunkValidationResult(
            passed=False,
            rms=0.01,
            peak_amplitude=0.1,
            voiced_ratio=0.1,  # below threshold
            duration_sec=0.5,
            failure_reason="voiced_ratio 10.0% < 30.0%",
        )

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.validate_chunk_audio", return_value=failing_validation):
                with patch("app.workflows.steps.tts.settings") as mock_settings:
                    mock_settings.tts_ellipsis_pause_sec = 0.1
                    mock_settings.tts_exaggeration = 0.5
                    mock_settings.tts_cfg_weight = 0.5
                    mock_settings.tts_temperature = 0.8
                    mock_settings.tts_repetition_penalty = 1.2
                    mock_settings.tts_min_p = 0.05
                    mock_settings.tts_top_p = 1.0
                    mock_settings.tts_seed = 0
                    result = await _synthesize_chunk_with_pauses(
                        client=MagicMock(),
                        chunk_text="hello... world",
                        chunk_index=0,
                        voice_name="v.wav",
                        max_retries=0,
                    )

        assert result.validation_passed is False
        assert abs(result.duration_sec - 0.5) < 0.01

    @pytest.mark.asyncio
    async def test_too_many_ellipses_falls_through_unsplit(self) -> None:
        """Chunks with more than _MAX_ELLIPSIS_PIECES pieces are synthesized unsplit."""
        call_texts: list[str] = []

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            call_texts.append(kwargs["chunk_text"])
            return _make_chunk_result()

        # Build text with _MAX_ELLIPSIS_PIECES ellipses (produces MAX+1 pieces → over cap)
        over_cap_text = "... ".join(["word"] * (_MAX_ELLIPSIS_PIECES + 1))

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.settings") as mock_settings:
                mock_settings.tts_ellipsis_pause_sec = 0.1
                mock_settings.tts_exaggeration = 0.5
                mock_settings.tts_cfg_weight = 0.5
                mock_settings.tts_temperature = 0.8
                mock_settings.tts_repetition_penalty = 1.2
                mock_settings.tts_min_p = 0.05
                mock_settings.tts_top_p = 1.0
                mock_settings.tts_seed = 0
                await _synthesize_chunk_with_pauses(
                    client=MagicMock(),
                    chunk_text=over_cap_text,
                    chunk_index=0,
                    voice_name="v.wav",
                    max_retries=0,
                )

        # Should be called exactly once with the original unsplit text
        assert len(call_texts) == 1
        assert call_texts[0] == over_cap_text

    @pytest.mark.asyncio
    async def test_max_attempts_is_max_across_sub_pieces(self) -> None:
        """attempts_used reflects the worst-case sub-piece."""
        results = [
            _make_chunk_result(attempts_used=1),
            _make_chunk_result(attempts_used=3),
        ]
        idx = 0

        async def fake_retry(**kwargs: Any) -> ChunkSynthesisResult:
            nonlocal idx
            r = results[idx]
            idx += 1
            return r

        with patch("app.workflows.steps.tts._synthesize_with_retry", new=fake_retry):
            with patch("app.workflows.steps.tts.settings") as mock_settings:
                mock_settings.tts_ellipsis_pause_sec = 0.0
                mock_settings.tts_exaggeration = 0.5
                mock_settings.tts_cfg_weight = 0.5
                mock_settings.tts_temperature = 0.8
                mock_settings.tts_repetition_penalty = 1.2
                mock_settings.tts_min_p = 0.05
                mock_settings.tts_top_p = 1.0
                mock_settings.tts_seed = 0
                result = await _synthesize_chunk_with_pauses(
                    client=MagicMock(),
                    chunk_text="fast... slow",
                    chunk_index=0,
                    voice_name="v.wav",
                    max_retries=2,
                )

        assert result.attempts_used == 3

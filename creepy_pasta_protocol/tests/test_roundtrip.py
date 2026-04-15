"""Round-trip tests: every DTO model_validate(model_dump()) must survive intact."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from creepy_pasta_protocol import (
    PROTOCOL_VERSION,
    AudioBlobDTO,
    AudioFormat,
    ChunkDTO,
    ChunkSpec,
    ChunkValidationSnapshot,
    CreateRunRequest,
    CreateScriptRequest,
    CreateVoiceResponse,
    PatchRunRequest,
    ResolvedSettingsSnapshot,
    RunDetailDTO,
    RunStatus,
    RunSummaryDTO,
    RunWarnings,
    ScriptDTO,
    StorageBackend,
    UploadChunkAudioMetadata,
    UploadFinalAudioMetadata,
    VoiceDTO,
)


NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

SETTINGS = ResolvedSettingsSnapshot(
    reference_audio_filename="Robert.wav",
    output_format=AudioFormat.WAV,
    target_sample_rate=24000,
    split_text=True,
    chunk_size=120,
    temperature=0.8,
    exaggeration=0.5,
    cfg_weight=0.5,
    seed=0,
    speed_factor=1.0,
    language="en",
    enable_smart_stitching=True,
    sentence_pause_ms=200,
    crossfade_ms=20,
    safety_fade_ms=3,
    enable_dc_removal=False,
    dc_highpass_hz=15,
    peak_normalize_threshold=0.99,
    peak_normalize_target=0.95,
    enable_silence_trimming=False,
    enable_internal_silence_fix=False,
    enable_unvoiced_removal=False,
    max_reference_duration_sec=30,
    save_chunk_audio=True,
    save_final_audio=True,
    run_label=None,
    enable_chunk_validation=False,
    max_chunk_retries=3,
    chunk_validation_min_rms=1e-4,
    chunk_validation_min_peak=1e-3,
    chunk_validation_min_voiced_ratio=0.05,
    enable_text_normalization=False,
    text_normalization_model_id="Qwen/Qwen2.5-1.5B-Instruct",
    selected_chunk_indices=[1, 2, 3],
)


def rt(model: object) -> None:
    """Assert model_validate(model_dump()) produces an equal instance."""
    cls = type(model)
    dumped = model.model_dump()  # type: ignore[union-attr]
    restored = cls.model_validate(dumped)
    assert restored == model


def test_protocol_version() -> None:
    assert PROTOCOL_VERSION == "1"


def test_resolved_settings_snapshot() -> None:
    rt(SETTINGS)


def test_chunk_validation_snapshot() -> None:
    rt(ChunkValidationSnapshot(
        passed=True,
        duration_sec=2.5,
        rms_energy=0.05,
        peak_amplitude=0.8,
        voiced_ratio=0.72,
        failures=[],
    ))
    rt(ChunkValidationSnapshot(
        passed=False,
        duration_sec=0.1,
        rms_energy=1e-6,
        peak_amplitude=5e-4,
        voiced_ratio=0.01,
        failures=["rms_energy too low", "voiced_ratio too low"],
    ))


def test_create_script_request() -> None:
    rt(CreateScriptRequest(text="  Hello world  "))


def test_script_dto() -> None:
    rt(ScriptDTO(
        id="abc123",
        text="Hello world",
        text_sha256="deadbeef" * 8,
        char_count=11,
        created_at=NOW,
    ))


def test_voice_dto() -> None:
    rt(VoiceDTO(
        id="v1",
        filename="Robert.wav",
        audio_blob_id="blob1",
        duration_sec=5.2,
        created_at=NOW,
    ))


def test_create_voice_response() -> None:
    voice = VoiceDTO(
        id="v1",
        filename="Robert.wav",
        audio_blob_id="blob1",
        duration_sec=5.2,
        created_at=NOW,
    )
    rt(CreateVoiceResponse(voice=voice, created=True))
    rt(CreateVoiceResponse(voice=voice, created=False))


def test_chunk_spec() -> None:
    rt(ChunkSpec(chunk_index=1, text="The quick brown fox."))


def test_chunk_dto() -> None:
    validation = ChunkValidationSnapshot(
        passed=True, duration_sec=2.5, rms_energy=0.05,
        peak_amplitude=0.8, voiced_ratio=0.72, failures=[],
    )
    rt(ChunkDTO(
        id="c1", run_id="r1", chunk_index=0,
        text="Hello.", audio_blob_id="blob1",
        attempts_used=1, validation=validation,
    ))
    rt(ChunkDTO(
        id="c2", run_id="r1", chunk_index=1,
        text="World.", audio_blob_id=None,
        attempts_used=2, validation=None,
    ))


def test_audio_blob_dto() -> None:
    rt(AudioBlobDTO(
        id="blob1",
        storage_backend=StorageBackend.S3,
        storage_key="runs/abc/final.wav",
        sha256="deadbeef" * 8,
        byte_size=102400,
        mime_type="audio/wav",
        format=AudioFormat.WAV,
        sample_rate=24000,
        duration_sec=12.4,
        created_at=NOW,
    ))


def test_upload_chunk_audio_metadata() -> None:
    rt(UploadChunkAudioMetadata(
        run_id="r1",
        chunk_index=0,
        sha256="deadbeef" * 8,
        byte_size=51200,
        format=AudioFormat.WAV,
        sample_rate=24000,
        duration_sec=3.2,
        storage_backend=StorageBackend.LOCAL,
        storage_key="chunks/r1/0.wav",
        mime_type="audio/wav",
    ))


def test_upload_final_audio_metadata() -> None:
    rt(UploadFinalAudioMetadata(
        run_id="r1",
        sha256="deadbeef" * 8,
        byte_size=204800,
        format=AudioFormat.MP3,
        sample_rate=44100,
        duration_sec=45.0,
        storage_backend=StorageBackend.R2,
        storage_key="final/r1.mp3",
        mime_type="audio/mpeg",
    ))


def test_create_run_request() -> None:
    rt(CreateRunRequest(
        script_id="s1",
        voice_id="v1",
        run_label="test-run",
        settings=SETTINGS,
        output_format=AudioFormat.WAV,
        source_chunk_count=3,
        selected_chunk_indices=[1, 2, 3],
        normalized_text=None,
        pod_run_id="20260415_120000__Robert__abc123",
    ))
    rt(CreateRunRequest(
        script_id="s1",
        settings=SETTINGS,
        output_format=AudioFormat.MP3,
        source_chunk_count=1,
    ))


def test_patch_run_request() -> None:
    rt(PatchRunRequest(status=RunStatus.COMPLETED, completed_at=NOW, final_audio_id="blob1"))
    rt(PatchRunRequest(status=RunStatus.FAILED, error="TTS engine crashed"))
    rt(PatchRunRequest())


def test_run_warnings() -> None:
    rt(RunWarnings(warnings=["Chunk 2: used best-effort audio."]))
    rt(RunWarnings(warnings=[]))


def test_run_summary_dto() -> None:
    rt(RunSummaryDTO(
        id="r1",
        script_id="s1",
        voice_id="v1",
        status=RunStatus.COMPLETED,
        output_format=AudioFormat.WAV,
        source_chunk_count=3,
        selected_chunk_indices=[1, 2, 3],
        created_at=NOW,
        completed_at=NOW,
        pod_run_id="20260415_120000__Robert__abc123",
    ))


def test_run_detail_dto() -> None:
    chunks = [
        ChunkDTO(id="c1", run_id="r1", chunk_index=0, text="Hello.", attempts_used=1),
        ChunkDTO(id="c2", run_id="r1", chunk_index=1, text="World.", attempts_used=1),
    ]
    rt(RunDetailDTO(
        id="r1",
        script_id="s1",
        voice_id="v1",
        status=RunStatus.COMPLETED,
        output_format=AudioFormat.WAV,
        source_chunk_count=2,
        selected_chunk_indices=[1, 2],
        created_at=NOW,
        completed_at=NOW,
        settings=SETTINGS,
        warnings=["Chunk 2: best-effort."],
        final_audio_id="blob1",
        chunks=chunks,
    ))

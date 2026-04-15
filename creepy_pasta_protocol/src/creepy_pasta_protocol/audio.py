"""Audio blob and upload metadata DTOs."""

from __future__ import annotations

from datetime import datetime

from .common import AudioFormat, Frozen, StorageBackend


class AudioBlobDTO(Frozen):
    id: str
    storage_backend: StorageBackend
    storage_key: str
    sha256: str
    byte_size: int
    mime_type: str
    format: AudioFormat
    sample_rate: int
    duration_sec: float
    created_at: datetime


class UploadChunkAudioMetadata(Frozen):
    run_id: str
    chunk_index: int
    sha256: str
    byte_size: int
    format: AudioFormat
    sample_rate: int
    duration_sec: float
    storage_backend: StorageBackend
    storage_key: str
    mime_type: str


class UploadFinalAudioMetadata(Frozen):
    run_id: str
    sha256: str
    byte_size: int
    format: AudioFormat
    sample_rate: int
    duration_sec: float
    storage_backend: StorageBackend
    storage_key: str
    mime_type: str

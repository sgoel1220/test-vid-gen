"""Run-related wire DTOs."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import Field

from .chunks import ChunkDTO
from .common import AudioFormat, Frozen, RunStatus
from .settings import ResolvedSettingsSnapshot


class CreateRunRequest(Frozen):
    """Create a new run.

    Correlation keys: Either script_id (server UUID) OR script_sha256 must be provided.
    The server will resolve script_sha256 to the corresponding script UUID if needed.
    pod_run_id serves as an idempotency key for retries.
    """
    script_id: Optional[str] = None
    script_sha256: Optional[str] = None
    voice_id: Optional[str] = None
    run_label: Optional[str] = None
    settings: ResolvedSettingsSnapshot
    output_format: AudioFormat
    source_chunk_count: int
    selected_chunk_indices: List[int] = Field(default_factory=list)
    normalized_text: Optional[str] = None
    pod_run_id: Optional[str] = None


class PatchRunRequest(Frozen):
    status: Optional[RunStatus] = None
    error: Optional[str] = None
    final_audio_id: Optional[str] = None
    warnings: Optional[List[str]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class RunWarnings(Frozen):
    warnings: List[str]


class RunSummaryDTO(Frozen):
    id: str
    script_id: str
    voice_id: Optional[str] = None
    run_label: Optional[str] = None
    status: RunStatus
    output_format: AudioFormat
    source_chunk_count: int
    selected_chunk_indices: List[int] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    pod_run_id: Optional[str] = None


class RunDetailDTO(Frozen):
    id: str
    script_id: str
    voice_id: Optional[str] = None
    run_label: Optional[str] = None
    status: RunStatus
    output_format: AudioFormat
    source_chunk_count: int
    selected_chunk_indices: List[int] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    pod_run_id: Optional[str] = None
    settings: ResolvedSettingsSnapshot
    normalized_text: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    final_audio_id: Optional[str] = None
    chunks: List[ChunkDTO] = Field(default_factory=list)

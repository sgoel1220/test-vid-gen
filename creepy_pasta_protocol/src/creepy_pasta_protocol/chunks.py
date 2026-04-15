"""Chunk-related wire DTOs."""

from __future__ import annotations

from typing import Optional

from .common import Frozen
from .validation import ChunkValidationSnapshot


class ChunkSpec(Frozen):
    chunk_index: int
    text: str


class ChunkDTO(Frozen):
    id: str
    run_id: str
    chunk_index: int
    text: str
    audio_blob_id: Optional[str] = None
    attempts_used: int
    validation: Optional[ChunkValidationSnapshot] = None

"""creepy_pasta_protocol — single source of truth for wire-format DTOs."""

from __future__ import annotations

PROTOCOL_VERSION: str = "1"

from .audio import AudioBlobDTO, UploadChunkAudioMetadata, UploadFinalAudioMetadata
from .chunks import ChunkDTO, ChunkSpec
from .common import AudioFormat, Frozen, RunStatus, StorageBackend
from .runs import (
    CreateRunRequest,
    PatchRunRequest,
    RunDetailDTO,
    RunSummaryDTO,
    RunWarnings,
)
from .scripts import CreateScriptRequest, ScriptDTO
from .settings import ResolvedSettingsSnapshot
from .stories import (
    GenerateStoryRequest,
    PatchStoryRequest,
    StoryActDTO,
    StoryDetailDTO,
    StoryStatus,
    StorySummaryDTO,
)
from .validation import ChunkValidationSnapshot
from .voices import CreateVoiceResponse, VoiceDTO

__all__ = [
    "PROTOCOL_VERSION",
    # common
    "Frozen",
    "StorageBackend",
    "RunStatus",
    "AudioFormat",
    # settings
    "ResolvedSettingsSnapshot",
    # validation
    "ChunkValidationSnapshot",
    # scripts
    "CreateScriptRequest",
    "ScriptDTO",
    # voices
    "VoiceDTO",
    "CreateVoiceResponse",
    # runs
    "CreateRunRequest",
    "PatchRunRequest",
    "RunWarnings",
    "RunSummaryDTO",
    "RunDetailDTO",
    # chunks
    "ChunkSpec",
    "ChunkDTO",
    # stories
    "StoryStatus",
    "GenerateStoryRequest",
    "PatchStoryRequest",
    "StoryActDTO",
    "StorySummaryDTO",
    "StoryDetailDTO",
    # audio
    "AudioBlobDTO",
    "UploadChunkAudioMetadata",
    "UploadFinalAudioMetadata",
]

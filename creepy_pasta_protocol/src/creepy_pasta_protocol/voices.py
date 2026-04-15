"""Voice-related wire DTOs."""

from __future__ import annotations

from datetime import datetime

from .common import Frozen


class VoiceDTO(Frozen):
    id: str
    filename: str
    audio_blob_id: str
    duration_sec: float
    created_at: datetime


class CreateVoiceResponse(Frozen):
    voice: VoiceDTO
    created: bool

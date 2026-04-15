"""Script-related wire DTOs."""

from __future__ import annotations

from datetime import datetime

from .common import Frozen


class CreateScriptRequest(Frozen):
    text: str


class ScriptDTO(Frozen):
    id: str
    text: str
    text_sha256: str
    char_count: int
    created_at: datetime

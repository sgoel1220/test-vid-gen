"""Shared base class and wire-format enumerations."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class StorageBackend(str, Enum):
    S3 = "s3"
    R2 = "r2"
    LOCAL = "local"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AudioFormat(str, Enum):
    WAV = "wav"
    OPUS = "opus"
    MP3 = "mp3"

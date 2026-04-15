"""Application settings via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        description="Async-compatible Postgres DSN, e.g. postgresql+asyncpg://..."
    )
    metadata_api_key: str = Field(description="Bearer token required on all routes.")
    audio_storage_root: str = Field(
        default="/data/audio",
        description="Filesystem root for LocalFilesystemAudioStore.",
    )
    listen_host: str = Field(default="0.0.0.0")
    listen_port: int = Field(default=8080)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

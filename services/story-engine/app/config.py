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

    metadata_server_url: str = Field(
        description="Base URL for metadata-server, e.g. http://localhost:8080"
    )
    metadata_api_key: str = Field(
        description="Bearer token for metadata-server API."
    )
    story_engine_api_key: str = Field(
        description="Bearer token required on story-engine routes."
    )
    listen_host: str = Field(default="0.0.0.0")
    listen_port: int = Field(default=8090)
    max_concurrent_generations: int = Field(default=2)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

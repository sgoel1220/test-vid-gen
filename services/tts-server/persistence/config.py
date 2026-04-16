"""Pydantic-settings configuration for the persistence layer."""

from __future__ import annotations

from functools import lru_cache

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PersistenceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    metadata_api_url: AnyHttpUrl | None = None
    metadata_api_key: SecretStr | None = None
    metadata_timeout_seconds: float = 30.0
    metadata_max_retries: int = 5

    def is_enabled(self) -> bool:
        """Return True iff both URL and API key are configured."""
        return self.metadata_api_url is not None and self.metadata_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> PersistenceSettings:
    return PersistenceSettings()

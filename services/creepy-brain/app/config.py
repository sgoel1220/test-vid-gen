"""Configuration settings for Creepy Brain service"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8006
    reload: bool = False
    dev_mode: bool = False  # Enables dev-only endpoints (e.g. /api/workflows/test)

    # Logging settings
    json_logs: bool = True  # False for pretty dev logs, True for production JSON

    # Database settings
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "creepy_brain"
    postgres_password: str = "dev_password"
    postgres_db: str = "creepy_brain"
    db_echo: bool = False

    # Hatchet workflow engine settings
    hatchet_client_token: str = ""

    # GPU provider settings
    runpod_api_key: str = ""

    # LLM settings
    llm_provider: Literal["anthropic", "openrouter"] = "anthropic"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    llm_model: str = "claude-opus-4-6"
    max_concurrent_generations: int = 2

    @property
    def database_url(self) -> str:
        """Construct async postgres connection URL"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# Global settings instance
settings = Settings()

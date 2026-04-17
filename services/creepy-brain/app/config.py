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

    # GPU pod configuration
    gpu_type: str = "NVIDIA RTX A4000"
    gpu_cloud_type: str = "COMMUNITY"  # COMMUNITY or SECURE
    gpu_image: str = "ghcr.io/sgoel1220/tts-server:main"
    gpu_container_disk_gb: int = 25
    gpu_volume_gb: int = 0  # No persistent volume
    gpu_port: int = 8005

    # TTS settings
    tts_default_voice: str = "old_man_low.wav"

    # LLM settings
    llm_provider: Literal["anthropic", "openrouter"] = "openrouter"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    llm_model: str = "meta-llama/llama-3.1-8b-instruct"  # ~$0.05/1M tokens
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

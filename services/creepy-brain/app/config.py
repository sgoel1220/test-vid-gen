"""Configuration settings for Creepy Brain service"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.validation_limits import (
    DEFAULT_STORY_TARGET_WORD_COUNT,
    WORKFLOW_TARGET_WORD_COUNT_MAX,
    WORKFLOW_TARGET_WORD_COUNT_MIN,
)



GpuTierName = Literal["small", "medium", "large"]


class GpuTier(BaseModel):
    """Ordered list of GPU types to try, cheapest first."""

    gpu_types: list[str]  # First = preferred (cheapest), last = fallback (pricier)


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
    postgres_password: str = Field(default="", repr=False)
    postgres_db: str = "creepy_brain"
    db_echo: bool = False

    # GPU provider settings
    gpu_provider: str = "runpod"
    runpod_api_key: str = ""
    vastai_api_key: str = ""
    vastai_min_reliability: float = 0.99
    vastai_max_dph: float = 2.0
    vastai_geo: str = ""
    vastai_cuda_min: float = 12.0
    vastai_max_inet_down_cost_per_tb: float = 0.004  # $/TB; 0 = no filter

    # GPU pod configuration
    gpu_type: str = "NVIDIA RTX A4000"
    gpu_type_fallbacks: list[str] = Field(default_factory=lambda: ["NVIDIA RTX 3080 Ti"])
    gpu_cloud_type: str = "COMMUNITY"  # COMMUNITY or SECURE
    gpu_image: str = "ghcr.io/sgoel1220/tts-server:main"
    gpu_container_disk_gb: int = 25
    gpu_volume_gb: int = 0  # No persistent volume
    gpu_port: int = 8005
    # Per-step GPU tiers — override via env vars as JSON lists, e.g.:
    # GPU_TIER_SMALL='["NVIDIA RTX 3060","NVIDIA RTX 4060 Ti"]'
    gpu_tier_small: list[str] = Field(default_factory=lambda: [
        "NVIDIA RTX 3060",
        "NVIDIA RTX 4060 Ti",
        "NVIDIA RTX 3070",
        "NVIDIA RTX 4070 Super",
        "NVIDIA RTX 3080",
    ])
    gpu_tier_medium: list[str] = Field(default_factory=lambda: [
        "NVIDIA RTX A4000",
        "NVIDIA RTX 4070 Ti",
        "NVIDIA RTX 3080 Ti",
        "NVIDIA RTX A5000",
    ])
    gpu_tier_large: list[str] = Field(default_factory=lambda: [
        "NVIDIA RTX A5000",
        "NVIDIA RTX A6000",
        "NVIDIA RTX 4090",
    ])

    def gpu_tier(self, name: "GpuTierName") -> GpuTier:
        """Resolve a named GPU tier to its ordered list of GPU types."""
        mapping: dict[str, list[str]] = {
            "small": self.gpu_tier_small,
            "medium": self.gpu_tier_medium,
            "large": self.gpu_tier_large,
        }
        return GpuTier(gpu_types=mapping[name])

    # TTS settings
    tts_default_voice: str = "old_man_low.wav"
    tts_seed: int = 1234
    tts_exaggeration: float = 0.55
    tts_cfg_weight: float = 0.45
    tts_temperature: float = 0.82
    tts_repetition_penalty: float = 1.2
    tts_min_p: float = 0.05
    tts_top_p: float = 1.0
    tts_chunk_size: int = 300

    # Scene grouping settings (for image generation)
    chunks_per_scene: Annotated[int, Field(ge=1)] = 7  # Number of TTS chunks per image scene

    # Image server GPU pod configuration (separate from TTS)
    image_server_image: str = "ghcr.io/sgoel1220/comfyui-vast:main"
    image_server_port: int = 8006
    image_width: int = 1280
    image_height: int = 720

    # ComfyUI RunPod Serverless endpoint (image generation)
    comfyui_endpoint_id: str = "mug4b9qd6t1p1h"
    comfyui_api_key: str = ""  # Set via COMFYUI_API_KEY env var

    # Music server GPU pod configuration
    music_server_image: str = "ghcr.io/sgoel1220/music-server:main"
    music_server_port: int = 8007
    # Baked model weights add ~9.4 GiB to the image; 50 GB gives adequate headroom.
    music_server_container_disk_gb: int = 50
    sfx_server_image: str = "ghcr.io/sgoel1220/sfx-server:main"
    sfx_server_port: int = 8008
    music_volume_db: float = -20.0  # static duck level under narration
    sfx_volume_db: float = -6.0  # SFX louder than music bed

    # GPU pod lifecycle
    pod_ready_timeout_sec: int = 600  # 10 minutes to wait for pod ready (cold image pull)

    # Cost tracking settings
    daily_cost_alert_threshold_cents: int = 1000  # $10
    workflow_cost_limit_cents: int = 500  # $5

    # LLM settings
    llm_provider: Literal["anthropic", "openrouter"] = "openrouter"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    llm_model: str = "meta-llama/llama-3.1-8b-instruct"  # ~$0.05/1M tokens
    max_concurrent_generations: int = 2

    # Story generation defaults
    story_target_word_count: int = Field(
        default=DEFAULT_STORY_TARGET_WORD_COUNT,
        ge=WORKFLOW_TARGET_WORD_COUNT_MIN,
        le=WORKFLOW_TARGET_WORD_COUNT_MAX,
    )

    @property
    def database_url(self) -> str:
        """Construct async postgres connection URL"""
        if not self.postgres_password:
            raise RuntimeError("POSTGRES_PASSWORD must be set")
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# Global settings instance
settings = Settings()

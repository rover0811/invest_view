from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AiSignalSettings(BaseSettings):
    """Environment configuration for the AI signal service."""

    model_config = SettingsConfigDict(
        env_prefix="AI_SIGNAL_",
        env_file=None,
        extra="ignore",
    )

    database_url: str
    kafka_bootstrap_servers: str
    schema_registry_url: str
    log_level: str = "INFO"


def load_settings() -> AiSignalSettings:
    env_only: dict[str, str] = {}
    return AiSignalSettings(**env_only)

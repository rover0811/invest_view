from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StreamDetectionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STREAM_", env_file=".env", extra="ignore")

    kafka_bootstrap: str = Field(default="localhost:9092")
    schema_registry_url: str = Field(default="http://localhost:8081")

    source_topic: str = Field(default="stock-ticks")
    alert_topic: str = Field(default="stock-alerts")
    pattern_topic: str = Field(default="stock-patterns")

    window_size_seconds: int = Field(default=300)
    window_slide_seconds: int = Field(default=60)

    parallelism: int = Field(default=2)
    checkpoint_interval_ms: int = Field(default=60_000)

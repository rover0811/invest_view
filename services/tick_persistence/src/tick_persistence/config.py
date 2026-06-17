from pydantic_settings import BaseSettings, SettingsConfigDict


class TickPersistenceConfig(BaseSettings):
    """Env-var config (prefix ``TICK_PERSISTENCE_``); database_url, kafka_bootstrap_servers and schema_registry_url are required."""

    model_config = SettingsConfigDict(
        env_prefix="TICK_PERSISTENCE_",
        env_file=None,  # pydantic-settings would otherwise auto-read a stray .env; force explicit env vars
        extra="ignore",
    )

    database_url: str

    kafka_bootstrap_servers: str
    kafka_topic: str = "stock-ticks"
    kafka_consumer_group: str = "tick-persistence-v1"
    kafka_auto_offset_reset: str = "earliest"
    batch_size: int = 500
    poll_timeout: float = 1.0
    max_poll_interval_ms: int = 300_000

    schema_registry_url: str
    avro_schema_path: str = "schemas/stock-ticks.avsc"

    metrics_enabled: bool = True
    metrics_port: int = 9090
    metrics_addr: str = "0.0.0.0"
    freshness_refresh_interval_seconds: float = 5.0
    reconciliation_log_interval_seconds: float = 60.0

    log_level: str = "INFO"

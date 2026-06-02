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

    schema_registry_url: str
    avro_schema_path: str = "schemas/stock-ticks.avsc"

    log_level: str = "INFO"

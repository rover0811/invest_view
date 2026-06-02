from pydantic_settings import BaseSettings, SettingsConfigDict


class EventPatternPersistenceConfig(BaseSettings):
    """Env-var config (prefix ``EVENT_PATTERN_PERSISTENCE_``); database_url, kafka_bootstrap_servers and schema_registry_url are required."""

    model_config = SettingsConfigDict(
        env_prefix="EVENT_PATTERN_PERSISTENCE_",
        env_file=None,  # pydantic-settings would otherwise auto-read a stray .env; force explicit env vars
        extra="ignore",
    )

    database_url: str

    kafka_bootstrap_servers: str
    kafka_topic: str = "stock-patterns"
    kafka_consumer_group: str = "event-pattern-persistence-v1"
    kafka_auto_offset_reset: str = "earliest"

    schema_registry_url: str
    avro_schema_path: str = "schemas/stock-patterns.avsc"

    log_level: str = "INFO"

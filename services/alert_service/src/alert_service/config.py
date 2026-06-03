from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class AlertServiceConfig(BaseSettings):
    """Configuration for alert_service.

    Loaded from environment variables with prefix ``ALERT_SERVICE_``.
    Required (no default): database_url, kafka_bootstrap_servers, jwt_secret, schema_registry_url.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="ALERT_SERVICE_",
        env_file=None,  # don't auto-load .env - explicit via env vars
        extra="ignore",
    )

    # Database
    database_url: str  # e.g. postgresql+asyncpg://user:pass@host:5432/db

    # Kafka
    kafka_bootstrap_servers: str  # e.g. localhost:9092
    kafka_topic: str = "stock-alerts"
    kafka_consumer_group: str = "alert-service-v1"
    kafka_auto_offset_reset: str = "latest"

    # Schema Registry
    schema_registry_url: str  # e.g. http://localhost:8081
    avro_schema_path: str = "schemas/stock-alerts.avsc"

    # JWT
    jwt_secret: str  # HS256 shared secret
    jwt_algorithm: str = "HS256"
    jwt_user_id_claim: str = "sub"

    # Vertex AI / Gemini (agent layer)
    # ADC is provided via the GOOGLE_APPLICATION_CREDENTIALS env var
    # (google-genai auto-detects it; no API-key style auth is used).
    gcp_project: str | None = None
    gcp_location: str = "us-central1"
    gemini_model_id: str = "gemini-2.5-flash"
    gemini_temperature: float = 1.0
    gemini_max_output_tokens: int = 8192

    # CORS
    allow_origins: list[str] = []

    # HTTP
    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # Test seams
    fanout_fail_after_alert: bool = False

    # Logging
    log_level: str = "INFO"

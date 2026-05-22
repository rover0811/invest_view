from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlertServingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALERT_SERVING_", env_file=".env", extra="ignore")

    kafka_bootstrap: str = Field(default="localhost:9092")
    schema_registry_url: str = Field(default="http://localhost:8081")

    alert_topic: str = Field(default="stock-alerts")
    pattern_topic: str = Field(default="stock-patterns")
    consumer_group: str = Field(default="alert-serving")

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)

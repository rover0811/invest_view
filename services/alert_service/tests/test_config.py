import os
import pytest
from pydantic import ValidationError
from alert_service.config import AlertServiceConfig


def test_config_requires_database_url(monkeypatch):
    """Without ALERT_SERVICE_DATABASE_URL, ValidationError is raised."""
    for k in list(os.environ):
        if k.startswith("ALERT_SERVICE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("ALERT_SERVICE_SCHEMA_REGISTRY_URL", "http://localhost:8081")
    monkeypatch.setenv("ALERT_SERVICE_JWT_SECRET", "test-secret-32chars-min-for-hs256")
    with pytest.raises(ValidationError):
        AlertServiceConfig()


def test_config_loads_with_required_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("ALERT_SERVICE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_SERVICE_DATABASE_URL", "postgresql+asyncpg://x:y@h/d")
    monkeypatch.setenv("ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("ALERT_SERVICE_SCHEMA_REGISTRY_URL", "http://localhost:8081")
    monkeypatch.setenv("ALERT_SERVICE_JWT_SECRET", "test-secret-32chars-min-for-hs256")
    cfg = AlertServiceConfig()
    assert cfg.kafka_topic == "stock-alerts"
    assert cfg.jwt_algorithm == "HS256"
    assert cfg.http_port == 8000


def test_config_kafka_topic_override(monkeypatch):
    for k in list(os.environ):
        if k.startswith("ALERT_SERVICE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_SERVICE_DATABASE_URL", "postgresql+asyncpg://x:y@h/d")
    monkeypatch.setenv("ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("ALERT_SERVICE_SCHEMA_REGISTRY_URL", "http://localhost:8081")
    monkeypatch.setenv("ALERT_SERVICE_JWT_SECRET", "test-secret-32chars-min-for-hs256")
    monkeypatch.setenv("ALERT_SERVICE_KAFKA_TOPIC", "custom-alerts")
    cfg = AlertServiceConfig()
    assert cfg.kafka_topic == "custom-alerts"

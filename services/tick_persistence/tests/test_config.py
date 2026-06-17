import os

import pytest
from pydantic import ValidationError

from tick_persistence.config import TickPersistenceConfig


def _clear_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("TICK_PERSISTENCE_"):
            monkeypatch.delenv(k, raising=False)


def _set_required(monkeypatch):
    monkeypatch.setenv("TICK_PERSISTENCE_DATABASE_URL", "postgresql+asyncpg://x:y@h:5432/d")
    monkeypatch.setenv("TICK_PERSISTENCE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("TICK_PERSISTENCE_SCHEMA_REGISTRY_URL", "http://localhost:8081")


def test_config_requires_database_url(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("TICK_PERSISTENCE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("TICK_PERSISTENCE_SCHEMA_REGISTRY_URL", "http://localhost:8081")
    with pytest.raises(ValidationError):
        TickPersistenceConfig()


def test_config_defaults(monkeypatch):
    _clear_env(monkeypatch)
    _set_required(monkeypatch)
    cfg = TickPersistenceConfig()
    assert cfg.kafka_topic == "stock-ticks"
    assert cfg.kafka_consumer_group == "tick-persistence-v1"
    assert cfg.kafka_auto_offset_reset == "earliest"
    assert cfg.batch_size == 500
    assert cfg.poll_timeout == 1.0
    assert cfg.max_poll_interval_ms == 300_000
    assert cfg.avro_schema_path == "schemas/stock-ticks.avsc"
    assert cfg.metrics_enabled is True
    assert cfg.metrics_port == 9090
    assert cfg.metrics_addr == "0.0.0.0"
    assert cfg.freshness_refresh_interval_seconds == 5.0
    assert cfg.log_level == "INFO"


def test_config_overrides(monkeypatch):
    _clear_env(monkeypatch)
    _set_required(monkeypatch)
    monkeypatch.setenv("TICK_PERSISTENCE_KAFKA_TOPIC", "custom-ticks")
    monkeypatch.setenv("TICK_PERSISTENCE_KAFKA_AUTO_OFFSET_RESET", "latest")
    monkeypatch.setenv("TICK_PERSISTENCE_BATCH_SIZE", "250")
    monkeypatch.setenv("TICK_PERSISTENCE_POLL_TIMEOUT", "0.5")
    monkeypatch.setenv("TICK_PERSISTENCE_MAX_POLL_INTERVAL_MS", "600000")
    monkeypatch.setenv("TICK_PERSISTENCE_METRICS_ENABLED", "false")
    monkeypatch.setenv("TICK_PERSISTENCE_METRICS_PORT", "8123")
    monkeypatch.setenv("TICK_PERSISTENCE_FRESHNESS_REFRESH_INTERVAL_SECONDS", "2.5")
    cfg = TickPersistenceConfig()
    assert cfg.kafka_topic == "custom-ticks"
    assert cfg.kafka_auto_offset_reset == "latest"
    assert cfg.batch_size == 250
    assert cfg.poll_timeout == 0.5
    assert cfg.max_poll_interval_ms == 600_000
    assert cfg.metrics_enabled is False
    assert cfg.metrics_port == 8123
    assert cfg.freshness_refresh_interval_seconds == 2.5

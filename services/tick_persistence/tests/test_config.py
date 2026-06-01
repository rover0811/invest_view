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
    assert cfg.avro_schema_path == "schemas/stock-ticks.avsc"
    assert cfg.log_level == "INFO"


def test_config_overrides(monkeypatch):
    _clear_env(monkeypatch)
    _set_required(monkeypatch)
    monkeypatch.setenv("TICK_PERSISTENCE_KAFKA_TOPIC", "custom-ticks")
    monkeypatch.setenv("TICK_PERSISTENCE_KAFKA_AUTO_OFFSET_RESET", "latest")
    cfg = TickPersistenceConfig()
    assert cfg.kafka_topic == "custom-ticks"
    assert cfg.kafka_auto_offset_reset == "latest"

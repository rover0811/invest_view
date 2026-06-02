import os

import pytest
from pydantic import ValidationError

from event_pattern_persistence.config import EventPatternPersistenceConfig


def _clear_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("EVENT_PATTERN_PERSISTENCE_"):
            monkeypatch.delenv(k, raising=False)


def _set_required(monkeypatch):
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_DATABASE_URL", "postgresql+asyncpg://x:y@h:5432/d")
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_SCHEMA_REGISTRY_URL", "http://localhost:8081")


def test_config_requires_database_url(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_SCHEMA_REGISTRY_URL", "http://localhost:8081")
    with pytest.raises(ValidationError):
        EventPatternPersistenceConfig()


def test_config_defaults(monkeypatch):
    _clear_env(monkeypatch)
    _set_required(monkeypatch)
    cfg = EventPatternPersistenceConfig()
    assert cfg.kafka_topic == "stock-patterns"
    assert cfg.kafka_consumer_group == "event-pattern-persistence-v1"
    assert cfg.kafka_auto_offset_reset == "earliest"
    assert cfg.avro_schema_path == "schemas/stock-patterns.avsc"
    assert cfg.log_level == "INFO"


def test_config_overrides(monkeypatch):
    _clear_env(monkeypatch)
    _set_required(monkeypatch)
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_KAFKA_TOPIC", "custom-patterns")
    monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_KAFKA_AUTO_OFFSET_RESET", "latest")
    cfg = EventPatternPersistenceConfig()
    assert cfg.kafka_topic == "custom-patterns"
    assert cfg.kafka_auto_offset_reset == "latest"

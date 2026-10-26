from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

AiSignalSettings = importlib.import_module("ai_signal.config").AiSignalSettings


def test_settings_use_ai_signal_prefix(monkeypatch) -> None:
    monkeypatch.setenv(
        "AI_SIGNAL_DATABASE_URL", "postgresql+asyncpg://user:pass@db/app"
    )
    monkeypatch.setenv("AI_SIGNAL_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("AI_SIGNAL_SCHEMA_REGISTRY_URL", "http://registry:8081")
    monkeypatch.setenv("AI_SIGNAL_LOG_LEVEL", "DEBUG")

    settings = AiSignalSettings()

    assert settings.database_url == "postgresql+asyncpg://user:pass@db/app"
    assert settings.log_level == "DEBUG"
    assert settings.kafka_bootstrap_servers == "kafka:9092"


def test_settings_require_service_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AI_SIGNAL_DATABASE_URL",
        "AI_SIGNAL_KAFKA_BOOTSTRAP_SERVERS",
        "AI_SIGNAL_SCHEMA_REGISTRY_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        AiSignalSettings()

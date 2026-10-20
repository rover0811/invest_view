from __future__ import annotations

import asyncio
import importlib

import pytest

from ai_signal.config import AiSignalSettings, load_settings
from ai_signal.runners import RUNNERS, Runner, run_selected, runner_names, select

entrypoint = importlib.import_module("ai_signal.__main__")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SIGNAL_DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("AI_SIGNAL_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    monkeypatch.setenv("AI_SIGNAL_SCHEMA_REGISTRY_URL", "http://sr:8081")


def test_parse_args_accepts_all_runner() -> None:
    assert entrypoint.parse_args(["all"]).runner == "all"


def test_registry_includes_outbox_relay() -> None:
    assert "outbox_relay" in runner_names()
    assert runner_names()[0] == "all"
    assert len({runner.name for runner in RUNNERS}) == len(RUNNERS)


@pytest.mark.usefixtures("env")
def test_main_dispatches_selected_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_run_selected(name: str, settings: AiSignalSettings) -> None:
        assert settings.schema_registry_url == "http://sr:8081"
        calls.append(name)

    monkeypatch.setattr(entrypoint, "run_selected", fake_run_selected)
    assert entrypoint.main(["all"]) == 0
    assert calls == ["all"]


def test_empty_registry_starts_nothing() -> None:
    settings = AiSignalSettings(
        database_url="postgresql+asyncpg://u:p@h/db",
        kafka_bootstrap_servers="kafka:9092",
        schema_registry_url="http://sr:8081",
    )
    asyncio.run(run_selected("all", settings, ()))


@pytest.mark.usefixtures("env")
def test_registered_runners_start_without_dispatch_changes() -> None:
    started: list[str] = []

    def make(name: str) -> Runner:
        async def run(settings: AiSignalSettings) -> None:
            assert settings.kafka_bootstrap_servers == "kafka:9092"
            started.append(name)

        return Runner(name, run)

    registry = (make("a"), make("b"))
    settings = load_settings()

    asyncio.run(run_selected("all", settings, registry))
    assert sorted(started) == ["a", "b"]

    started.clear()
    asyncio.run(run_selected("b", settings, registry))
    assert started == ["b"]
    assert runner_names(registry) == ("all", "a", "b")


def test_select_unknown_runner_raises() -> None:
    with pytest.raises(KeyError):
        _ = select("missing")

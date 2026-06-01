from __future__ import annotations

import importlib
from dataclasses import dataclass

import pytest


@dataclass
class _FakeConfig:
    log_level: str = "INFO"


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeConsumer:
    def __init__(self, fatal_error: BaseException | None = None) -> None:
        self.fatal_error = fatal_error
        self.started = False
        self.waited = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def wait_dead(self) -> None:
        self.waited = True

    def stop(self) -> None:
        self.stopped = True


class _FakeContainer:
    def __init__(self, _config: _FakeConfig, *, fatal_error: BaseException | None = None) -> None:
        self.consumer = _FakeConsumer(fatal_error)
        self.engine = _FakeEngine()


async def test_main_async_returns_cleanly_and_disposes(monkeypatch):
    entrypoint = importlib.import_module("tick_persistence.__main__")
    seen: dict[str, _FakeContainer] = {}

    def make_container(config: _FakeConfig) -> _FakeContainer:
        container = _FakeContainer(config)
        seen["container"] = container
        return container

    monkeypatch.setattr(entrypoint, "TickPersistenceConfig", _FakeConfig)
    monkeypatch.setattr(entrypoint, "Container", make_container)

    await entrypoint.main_async()

    container = seen["container"]
    assert container.consumer.started is True
    assert container.consumer.waited is True
    assert container.consumer.stopped is True
    assert container.engine.disposed is True


async def test_main_async_raises_consumer_fatal_error_after_cleanup(monkeypatch):
    entrypoint = importlib.import_module("tick_persistence.__main__")
    fatal = RuntimeError("consumer died")
    seen: dict[str, _FakeContainer] = {}

    def make_container(config: _FakeConfig) -> _FakeContainer:
        container = _FakeContainer(config, fatal_error=fatal)
        seen["container"] = container
        return container

    monkeypatch.setattr(entrypoint, "TickPersistenceConfig", _FakeConfig)
    monkeypatch.setattr(entrypoint, "Container", make_container)

    with pytest.raises(RuntimeError, match="consumer died") as exc_info:
        await entrypoint.main_async()

    assert exc_info.value is fatal
    container = seen["container"]
    assert container.consumer.started is True
    assert container.consumer.waited is True
    assert container.consumer.stopped is True
    assert container.engine.disposed is True

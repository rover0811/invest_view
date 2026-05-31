import asyncio
import importlib
from unittest.mock import MagicMock

import pytest


def test_main_module_imports_without_side_effects():
    module = importlib.import_module("alert_service.__main__")

    assert hasattr(module, "main")


class _FakeServer:
    instances = []

    def __init__(self, _config):
        self.should_exit = False
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.instances.append(self)

    async def serve(self):
        self.started.set()
        while not self.should_exit:
            await asyncio.sleep(0.001)
        self.stopped.set()


class _DeadConsumer:
    def __init__(self) -> None:
        self._dead = asyncio.Event()
        self._fatal_error: BaseException | None = None

    async def wait_dead(self) -> None:
        await self._dead.wait()

    @property
    def fatal_error(self) -> BaseException | None:
        return self._fatal_error

    def die(self, error: BaseException) -> None:
        self._fatal_error = error
        self._dead.set()


@pytest.mark.asyncio
async def test_serve_stops_server_and_reraises_consumer_fatal(monkeypatch):
    main_module = importlib.import_module("alert_service.__main__")
    _FakeServer.instances = []
    monkeypatch.setattr(main_module.uvicorn, "Config", lambda *args, **kwargs: (args, kwargs))
    monkeypatch.setattr(main_module.uvicorn, "Server", _FakeServer)

    consumer = _DeadConsumer()
    container = MagicMock()
    container.alert_consumer = consumer
    config = MagicMock()
    config.http_host = "127.0.0.1"
    config.http_port = 8000
    config.log_level = "debug"

    task = asyncio.create_task(main_module._serve(config, container, MagicMock()))
    while not _FakeServer.instances and not task.done():
        await asyncio.sleep(0)
    assert not task.done()
    server = _FakeServer.instances[0]
    await asyncio.wait_for(server.started.wait(), timeout=0.1)

    error = RuntimeError("consumer died")
    consumer.die(error)

    with pytest.raises(RuntimeError, match="consumer died"):
        await task
    assert server.should_exit
    assert server.stopped.is_set()

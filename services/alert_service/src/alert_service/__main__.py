"""Entrypoint: load config, build container, run uvicorn."""
from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

from alert_service.api.app import create_app
from alert_service.config import AlertServiceConfig
from alert_service.container import Container


logger = logging.getLogger(__name__)


async def _supervise_consumer(server: uvicorn.Server, container: Container) -> None:
    await container.alert_consumer.wait_dead()
    if container.alert_consumer.fatal_error is not None:
        server.should_exit = True


async def _serve(config: AlertServiceConfig, container: Container, app: object) -> None:
    server_config = uvicorn.Config(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level=config.log_level.lower(),
    )
    server = uvicorn.Server(server_config)
    server_task = asyncio.create_task(server.serve(), name="alert-service-uvicorn")
    supervisor_task = asyncio.create_task(
        _supervise_consumer(server, container), name="alert-consumer-supervisor"
    )

    try:
        done, _pending = await asyncio.wait(
            {server_task, supervisor_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if supervisor_task in done:
            await supervisor_task
            await server_task
        else:
            await server_task

        if container.alert_consumer.fatal_error is not None:
            raise container.alert_consumer.fatal_error
    except Exception:
        logger.exception("alert service failed")
        raise
    finally:
        if not supervisor_task.done():
            supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor_task
        if not server_task.done():
            server.should_exit = True
            with contextlib.suppress(asyncio.CancelledError):
                await server_task


def main() -> None:
    config = AlertServiceConfig()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    container = Container(config)
    app = create_app(container)
    asyncio.run(_serve(config, container, app))


if __name__ == "__main__":
    main()

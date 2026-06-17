from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, cast

from tick_persistence.config import TickPersistenceConfig
from tick_persistence.container import Container
from tick_persistence.observability import start_metrics_server


async def main_async() -> None:
    config = cast(Any, TickPersistenceConfig)()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    container = Container(config)
    metrics_server = None
    freshness_task: asyncio.Task[None] | None = None
    try:
        if config.metrics_enabled:
            metrics_server, _ = start_metrics_server(
                container.metrics, config.metrics_port, config.metrics_addr
            )
            freshness_task = asyncio.create_task(
                container.freshness_monitor.run(), name="freshness-monitor"
            )
        await container.consumer.start()
        await container.consumer.wait_dead()
    finally:
        container.consumer.stop()
        if freshness_task is not None:
            container.freshness_monitor.stop()
            freshness_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await freshness_task
        if metrics_server is not None:
            metrics_server.shutdown()
        await container.engine.dispose()
    if container.consumer.fatal_error is not None:
        raise container.consumer.fatal_error


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

"""Entrypoint: load config, build container, run the stock-patterns consumer (no HTTP server)."""
from __future__ import annotations

import asyncio
import logging

from event_pattern_persistence.config import EventPatternPersistenceConfig
from event_pattern_persistence.container import Container


logger = logging.getLogger(__name__)


async def main_async() -> None:
    config = EventPatternPersistenceConfig()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    container = Container(config)
    await container.consumer.start()
    try:
        await container.consumer.wait_dead()
    finally:
        container.consumer.stop()
    if container.consumer.fatal_error is not None:
        raise container.consumer.fatal_error


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

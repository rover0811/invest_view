from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from tick_persistence.config import TickPersistenceConfig
from tick_persistence.container import Container


async def main_async() -> None:
    config = cast(Any, TickPersistenceConfig)()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    container = Container(config)
    try:
        await container.consumer.start()
        await container.consumer.wait_dead()
    finally:
        container.consumer.stop()
        await container.engine.dispose()
    if container.consumer.fatal_error is not None:
        raise container.consumer.fatal_error


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

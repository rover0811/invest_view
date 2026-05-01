import asyncio
import logging
import sys

from kis_ingestion.config import KISConfig
from kis_ingestion.container import create_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    try:
        config = KISConfig()
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        logger.error("Set KIS_APP_KEY and KIS_APP_SECRET environment variables")
        sys.exit(1)

    service = create_service(config)
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())

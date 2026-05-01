import asyncio
import logging
import signal

import httpx

from .connection_manager import KISConnectionManager
from .config import KISConfig
from .producer import StockTickProducer


logger = logging.getLogger(__name__)


class IngestionService:

    def __init__(
        self,
        config: KISConfig,
        connection_manager: KISConnectionManager,
        http_client: httpx.AsyncClient,
        producer: StockTickProducer | None,
    ) -> None:
        self._config = config
        self._connection_manager = connection_manager
        self._http_client = http_client
        self._producer = producer

    async def run(self) -> None:
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown(loop)))
            except NotImplementedError:
                pass

        try:
            logger.info("Starting KIS ingestion service...")
            logger.info("Symbols: %s", self._config.watch_symbols)
            if self._producer is not None:
                logger.info("Kafka: enabled (broker=%s, topic=%s)", self._config.kafka_bootstrap_servers, self._config.kafka_topic)
            else:
                logger.warning("Kafka: disabled (set KIS_KAFKA_ENABLED=true to publish ticks)")
            # 단일 실행 진입점: approval key → websocket → subscribe → receive loop (SIGINT까지 블로킹)
            await self._connection_manager.start()
        except Exception:
            logger.exception("KIS ingestion service failed")
        finally:
            await self._shutdown(loop)

    async def _shutdown(self, loop: asyncio.AbstractEventLoop) -> None:
        logger.info("Shutting down...")
        await self._connection_manager.stop()
        if self._producer is not None:
            remaining = await loop.run_in_executor(None, self._producer.flush, 30.0)
            if remaining > 0:
                logger.warning("Kafka flush incomplete: %d messages not delivered", remaining)
            else:
                logger.info("Kafka producer flushed successfully")
        await self._http_client.aclose()

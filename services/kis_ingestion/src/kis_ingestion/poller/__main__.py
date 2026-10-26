import asyncio
import logging
import signal
import sys
from collections.abc import Sequence

import httpx

from kis_ingestion.config import KISConfig
from kis_ingestion.producer import AvroEventProducer
from kis_ingestion.token_manager import KISTokenManager

from .base import PollerTask, run_pollers
from .config import PollerConfig
from .kis_client import KISRestClient
from .rate_limiter import SharedRateLimiter
from .registry import POLLER_FACTORIES, PollerDependencies, PollerFactory
from .symbols import ReferenceSymbolSource, SymbolSource

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class ProducerPool:
    def __init__(self, kis_config: KISConfig, poller_config: PollerConfig) -> None:
        self._kis_config: KISConfig = kis_config
        self._poller_config: PollerConfig = poller_config
        self._producers: list[AvroEventProducer] = []

    def producer_for(self, topic: str, schema_file: str) -> AvroEventProducer:
        producer = AvroEventProducer(
            bootstrap_servers=self._kis_config.kafka_bootstrap_servers,
            topic=topic,
            schema_path=str(self._poller_config.schema_dir / schema_file),
            schema_registry_url=self._kis_config.schema_registry_url,
        )
        self._producers.append(producer)
        return producer

    def flush_all(self, timeout: float) -> None:
        for producer in self._producers:
            remaining = producer.flush(timeout)
            if remaining > 0:
                logger.warning("Kafka flush incomplete topic=%s pending=%d", producer.topic, remaining)


def build_pollers(
    kis_config: KISConfig,
    poller_config: PollerConfig,
    http_client: httpx.AsyncClient,
    producer_pool: ProducerPool,
    symbol_source: SymbolSource | None = None,
    factories: Sequence[PollerFactory] = POLLER_FACTORIES,
) -> list[PollerTask]:
    token_manager = KISTokenManager(
        base_url=poller_config.rest_base_url,
        app_key=kis_config.app_key,
        app_secret=kis_config.app_secret,
        http_client=http_client,
    )
    kis_client = KISRestClient(
        base_url=poller_config.rest_base_url,
        app_key=kis_config.app_key,
        app_secret=kis_config.app_secret,
        token_manager=token_manager,
        http_client=http_client,
        rate_limiter=SharedRateLimiter(poller_config.rate_limit_per_second),
    )
    deps = PollerDependencies(
        kis_config=kis_config,
        poller_config=poller_config,
        kis_client=kis_client,
        symbol_source=symbol_source or ReferenceSymbolSource(poller_config.database_url),
        producer_for=producer_pool.producer_for,
    )
    return [factory(deps) for factory in factories]


async def main() -> None:
    try:
        kis_config = KISConfig.model_validate({})
        poller_config = PollerConfig.model_validate({})
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        logger.error("Set KIS_APP_KEY, KIS_APP_SECRET and KIS_POLLER_DATABASE_URL")
        sys.exit(1)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    producer_pool = ProducerPool(kis_config, poller_config)
    async with httpx.AsyncClient(timeout=15.0) as http_client:
        pollers = build_pollers(kis_config, poller_config, http_client, producer_pool)
        logger.info("Starting pollers: %s", ", ".join(poller.name for poller in pollers))
        try:
            await run_pollers(pollers, stop)
        finally:
            await asyncio.to_thread(producer_pool.flush_all, poller_config.flush_timeout_seconds)


if __name__ == "__main__":
    asyncio.run(main())

from collections.abc import Callable
from dataclasses import dataclass

from kis_ingestion.config import KISConfig
from kis_ingestion.producer import AvroEventProducer

from .base import PollerTask
from .config import PollerConfig
from .kis_client import KISRestClient
from .news import NewsPoller
from .symbols import SymbolSource


@dataclass(frozen=True)
class PollerDependencies:
    kis_config: KISConfig
    poller_config: PollerConfig
    kis_client: KISRestClient
    symbol_source: SymbolSource
    producer_for: Callable[[str, str], AvroEventProducer]


PollerFactory = Callable[[PollerDependencies], PollerTask]


def create_news_poller(deps: PollerDependencies) -> PollerTask:
    config = deps.poller_config
    return NewsPoller(
        kis_client=deps.kis_client,
        symbol_source=deps.symbol_source,
        producer=deps.producer_for(config.news_topic, "news-raw.avsc"),
        interval_seconds=config.news_interval_seconds,
        max_pages=config.news_max_pages,
        flush_timeout_seconds=config.flush_timeout_seconds,
    )


POLLER_FACTORIES: tuple[PollerFactory, ...] = (
    create_news_poller,
)

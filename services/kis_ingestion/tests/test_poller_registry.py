import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import httpx
import pytest

from kis_ingestion.config import KISConfig
from kis_ingestion.poller.__main__ import ProducerPool, build_pollers
from kis_ingestion.poller.base import KST, run_poller
from kis_ingestion.poller.config import PollerConfig
from kis_ingestion.poller.news import NewsPoller
from kis_ingestion.poller.registry import POLLER_FACTORIES, create_news_poller


class FakeSymbols:
    async def list_symbols(self) -> list[str]:
        return ["005930"]


def test_registry_registers_only_news_poller_in_phase_one():
    assert POLLER_FACTORIES == (create_news_poller,)


@pytest.mark.asyncio
async def test_build_pollers_wires_news_poller_to_news_raw_topic():
    kis_config = KISConfig.model_validate({"app_key": "k", "app_secret": "s"})
    poller_config = PollerConfig.model_validate({"database_url": "postgresql://u:p@h/db"})
    pool = MagicMock(spec=ProducerPool)
    async with httpx.AsyncClient() as http_client:
        pollers = build_pollers(kis_config, poller_config, http_client, pool, symbol_source=FakeSymbols())

    assert [type(p) for p in pollers] == [NewsPoller]
    pool.producer_for.assert_called_once_with("news-raw", "news-raw.avsc")


def test_default_schema_dir_contains_news_raw_schema():
    poller_config = PollerConfig.model_validate({"database_url": "postgresql://u:p@h/db"})

    assert (poller_config.schema_dir / "news-raw.avsc").is_file()


class CountingPoller:
    def __init__(self, active: bool, stop: asyncio.Event, fail: bool = False) -> None:
        self.active = active
        self.stop = stop
        self.fail = fail
        self.runs = 0
        self.checks = 0

    @property
    def name(self) -> str:
        return "counting"

    @property
    def interval_seconds(self) -> float:
        return 0.0

    def is_active(self, now: datetime) -> bool:
        self.checks += 1
        if self.checks >= 3:
            self.stop.set()
        return self.active

    async def run_once(self, now: datetime) -> int:
        self.runs += 1
        if self.fail:
            raise RuntimeError("boom")
        return 1


@pytest.mark.asyncio
async def test_run_poller_skips_cycles_outside_active_window():
    stop = asyncio.Event()
    poller = CountingPoller(active=False, stop=stop)

    await run_poller(poller, stop, clock=lambda: datetime(2026, 10, 19, 22, 0, tzinfo=KST))

    assert poller.checks == 3
    assert poller.runs == 0


@pytest.mark.asyncio
async def test_run_poller_survives_cycle_failures():
    stop = asyncio.Event()
    poller = CountingPoller(active=True, stop=stop, fail=True)

    await run_poller(poller, stop, clock=lambda: datetime(2026, 10, 19, 10, 0, tzinfo=KST))

    assert poller.runs == 3

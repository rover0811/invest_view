from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tick_persistence.aggregation.ohlc import BUCKET_SIZE, FiveMinuteAggregator
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository

logger = logging.getLogger(__name__)


class TickHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tick_history_repo: TickHistoryRepository,
        snapshot_repo: SnapshotRepository,
        metrics_repo: Metrics5mRepository,
        aggregator: FiveMinuteAggregator,
    ) -> None:
        self._session_factory = session_factory
        self._tick_history_repo = tick_history_repo
        self._snapshot_repo = snapshot_repo
        self._metrics_repo = metrics_repo
        self._aggregator = aggregator
        self._hydrated_keys: set[tuple[str, datetime]] = set()

    async def handle(self, message: TickMessage) -> None:
        tick = message.value
        symbol = str(tick["symbol"])
        bucket_start = self._aggregator.bucket_start(str(tick["business_date"]), str(tick["trade_time"]))

        async with self._session_factory() as session, session.begin():
            inserted = await self._tick_history_repo.insert(session, message)
            if not inserted:
                logger.debug(
                    "duplicate tick bronze insert skipped topic=%s partition=%s offset=%s",
                    message.topic,
                    message.partition,
                    message.offset,
                )
                return

            await self._snapshot_repo.upsert_snapshot(session, tick)
            await self._hydrate_once(session, symbol, bucket_start)

            bucket_start, bar = self._aggregator.add_tick(tick)
            await self._metrics_repo.upsert_bar(session, symbol, bucket_start, bucket_start + BUCKET_SIZE, bar)

            for finalized_symbol, finalized_start, finalized_bar in self._aggregator.pop_finalized_bars():
                await self._metrics_repo.upsert_bar(
                    session,
                    finalized_symbol,
                    finalized_start,
                    finalized_start + BUCKET_SIZE,
                    finalized_bar,
                )

    async def _hydrate_once(self, session: AsyncSession, symbol: str, bucket_start: datetime) -> None:
        key = (symbol, bucket_start)
        if key in self._hydrated_keys:
            return
        self._hydrated_keys.add(key)
        if self._aggregator.has_bar(symbol, bucket_start):
            return
        existing = await self._metrics_repo.load_bar_state(session, symbol, bucket_start)
        if existing is not None:
            self._aggregator.hydrate(symbol, bucket_start, existing)


def make_tick_handler(
    session_factory: async_sessionmaker[AsyncSession],
    tick_history_repo: TickHistoryRepository,
    snapshot_repo: SnapshotRepository,
    metrics_repo: Metrics5mRepository,
    aggregator: FiveMinuteAggregator,
) -> Callable[[TickMessage], Awaitable[None]]:
    return TickHandler(
        session_factory=session_factory,
        tick_history_repo=tick_history_repo,
        snapshot_repo=snapshot_repo,
        metrics_repo=metrics_repo,
        aggregator=aggregator,
    ).handle

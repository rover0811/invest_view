from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tick_persistence.aggregation.ohlc import BUCKET_SIZE, KST, BarState, FiveMinuteAggregator
from tick_persistence.event_id import compute_event_id
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.quarantine import QuarantineRepository, QuarantinedTick
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import InsertedTick, TickHistoryRepository

if TYPE_CHECKING:
    from tick_persistence.observability.metrics import TickMetrics
    from tick_persistence.observability.reconciliation import ReconciliationLedger

logger = logging.getLogger(__name__)

_REQUIRED_TICK_FIELDS = (
    "symbol",
    "business_date",
    "trade_time",
    "price",
    "trade_volume",
    "cumulative_volume",
    "trade_type",
)


class TickHandler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tick_history_repo: TickHistoryRepository,
        snapshot_repo: SnapshotRepository,
        metrics_repo: Metrics5mRepository,
        aggregator: FiveMinuteAggregator,
        quarantine_repo: QuarantineRepository | None = None,
        metrics: TickMetrics | None = None,
        ledger: ReconciliationLedger | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tick_history_repo = tick_history_repo
        self._snapshot_repo = snapshot_repo
        self._metrics_repo = metrics_repo
        self._aggregator = aggregator
        self._quarantine_repo = quarantine_repo or QuarantineRepository()
        self._metrics = metrics
        self._ledger = ledger
        self._hydrated_keys: set[tuple[str, datetime]] = set()
        self._price_anomaly_count = 0

    @property
    def quarantined_count(self) -> int:
        return self._quarantine_repo.quarantined_count

    @property
    def price_anomaly_count(self) -> int:
        return self._price_anomaly_count

    async def handle(self, message: TickMessage) -> None:
        await self.handle_batch([message])

    async def handle_batch(self, messages: list[TickMessage]) -> None:
        batch_start = time.perf_counter()
        valid_messages, quarantined = _partition_messages(messages)
        if not valid_messages and not quarantined:
            return

        inserted_ticks: list[InsertedTick] = []
        txn_start = time.perf_counter()
        async with self._session_factory() as session, session.begin():
            if quarantined:
                await self._quarantine_repo.quarantine_many(session, quarantined)
            if valid_messages:
                await self._hydrate_batch(session, valid_messages)
                inserted_ticks = await self._tick_history_repo.insert_many(session, valid_messages)
                if inserted_ticks:
                    self._observe_price_anomalies(inserted_ticks)

                    latest_ticks: list[Mapping[str, object]] = [
                        t.value for t in _latest_ticks_by_symbol(inserted_ticks).values()
                    ]
                    await self._snapshot_repo.upsert_snapshots(session, latest_ticks)

                    changed_bars = self._add_inserted_ticks(inserted_ticks)
                    finalized_bars = {
                        (finalized_symbol, finalized_start): finalized_bar
                        for finalized_symbol, finalized_start, finalized_bar in self._aggregator.pop_finalized_bars()
                    }
                    merged = {**changed_bars, **finalized_bars}
                    bar_rows: list[tuple[str, datetime, datetime | None, BarState]] = [
                        (symbol, bucket_start, bucket_start + BUCKET_SIZE, bar)
                        for (symbol, bucket_start), bar in merged.items()
                    ]
                    await self._metrics_repo.upsert_bars(session, bar_rows)

                    latest_bucket_by_symbol: dict[str, datetime] = {}
                    for symbol, bucket_start in changed_bars:
                        latest_bucket_by_symbol[symbol] = max(bucket_start, latest_bucket_by_symbol.get(symbol, bucket_start))
                    for symbol, bucket_start in latest_bucket_by_symbol.items():
                        self._forget_hydrated_before(symbol, bucket_start)
                else:
                    logger.debug(
                        "duplicate tick batch bronze insert skipped size=%s",
                        len(valid_messages),
                    )

        if self._metrics is not None:
            now = time.perf_counter()
            self._metrics.db_transaction_duration_seconds.observe(now - txn_start)
            self._metrics.batch_duration_seconds.observe(now - batch_start)
        self._record_reconciliation(valid_messages, quarantined, inserted_ticks)

    async def _hydrate_batch(self, session: AsyncSession, messages: list[TickMessage]) -> None:
        seen: set[tuple[str, datetime]] = set()
        for message in messages:
            tick = message.value
            symbol = str(tick["symbol"])
            bucket_start = self._aggregator.bucket_start(str(tick["business_date"]), str(tick["trade_time"]))
            key = (symbol, bucket_start)
            if key in seen:
                continue
            seen.add(key)
            await self._hydrate_once(session, symbol, bucket_start)

    def _add_inserted_ticks(self, inserted_ticks: list[InsertedTick]) -> dict[tuple[str, datetime], BarState]:
        changed_bars: dict[tuple[str, datetime], BarState] = {}
        ordered_ticks = sorted(
            inserted_ticks,
            key=lambda tick: (
                str(tick.value["symbol"]),
                self._bucket_start(tick.value),
                tick.event_ts,
                tick.partition,
                tick.offset,
            ),
        )
        for index, inserted_tick in enumerate(ordered_ticks):
            tick = inserted_tick.value
            symbol = str(tick["symbol"])
            bucket_start, bar = self._aggregator.add_tick(
                tick,
                event_ts=inserted_tick.event_ts,
                partition=inserted_tick.partition,
                offset=inserted_tick.offset,
            )
            changed_bars[(symbol, bucket_start)] = bar

            next_tick = ordered_ticks[index + 1] if index + 1 < len(ordered_ticks) else None
            if bar.is_final and next_tick is not None and self._same_bucket(next_tick, symbol, bucket_start):
                self._aggregator.hydrate(symbol, bucket_start, bar)
        return changed_bars

    def _same_bucket(self, inserted_tick: InsertedTick, symbol: str, bucket_start: datetime) -> bool:
        return str(inserted_tick.value["symbol"]) == symbol and self._bucket_start(inserted_tick.value) == bucket_start

    def _bucket_start(self, tick: dict[str, object]) -> datetime:
        return self._aggregator.bucket_start(str(tick["business_date"]), str(tick["trade_time"]))

    async def _hydrate_once(self, session: AsyncSession, symbol: str, bucket_start: datetime) -> None:
        key = (symbol, bucket_start)
        if self._aggregator.has_bar(symbol, bucket_start):
            self._hydrated_keys.add(key)
            return
        self._hydrated_keys.add(key)
        existing = await self._metrics_repo.load_bar_state(session, symbol, bucket_start)
        if existing is not None:
            self._aggregator.hydrate(symbol, bucket_start, existing)

    def _forget_hydrated_before(self, symbol: str, bucket_start: datetime) -> None:
        self._hydrated_keys = {
            key for key in self._hydrated_keys if key[0] != symbol or key[1] >= bucket_start
        }

    def _observe_price_anomalies(self, inserted_ticks: list[InsertedTick]) -> None:
        for inserted_tick in inserted_ticks:
            try:
                price = int(inserted_tick.value["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if price <= 0:
                self._price_anomaly_count += 1
                logger.warning(
                    "non-positive tick price observed symbol=%s price=%s partition=%s offset=%s",
                    inserted_tick.value.get("symbol"),
                    price,
                    inserted_tick.partition,
                    inserted_tick.offset,
                )

    def _record_reconciliation(
        self,
        valid_messages: list[TickMessage],
        quarantined: list[QuarantinedTick],
        inserted_ticks: list[InsertedTick],
    ) -> None:
        if self._ledger is None:
            return
        valid_by_partition = _counts_by_partition(message.partition for message in valid_messages)
        inserted_by_partition = _counts_by_partition(tick.partition for tick in inserted_ticks)
        quarantine_by_partition = _counts_by_partition(entry.partition for entry in quarantined)
        for partition, count in inserted_by_partition.items():
            self._ledger.record_inserted(partition, count)
        for partition, count in quarantine_by_partition.items():
            self._ledger.record_quarantine(partition, count)
        for partition, valid_count in valid_by_partition.items():
            conflict = valid_count - inserted_by_partition.get(partition, 0)
            if conflict > 0:
                self._ledger.record_conflict(partition, conflict)

    def clear_state(self) -> None:
        self._aggregator.clear()
        self._hydrated_keys.clear()


def make_tick_handler(
    session_factory: async_sessionmaker[AsyncSession],
    tick_history_repo: TickHistoryRepository,
    snapshot_repo: SnapshotRepository,
    metrics_repo: Metrics5mRepository,
    aggregator: FiveMinuteAggregator,
    quarantine_repo: QuarantineRepository | None = None,
    metrics: TickMetrics | None = None,
    ledger: ReconciliationLedger | None = None,
) -> Callable[[TickMessage], Awaitable[None]]:
    return TickHandler(
        session_factory=session_factory,
        tick_history_repo=tick_history_repo,
        snapshot_repo=snapshot_repo,
        metrics_repo=metrics_repo,
        aggregator=aggregator,
        quarantine_repo=quarantine_repo,
        metrics=metrics,
        ledger=ledger,
    ).handle


def _counts_by_partition(partitions: Iterable[int]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for partition in partitions:
        counts[partition] = counts.get(partition, 0) + 1
    return counts


def _event_ts(tick: dict[str, object]) -> datetime:
    return datetime.strptime(f"{tick['business_date']}{tick['trade_time']}", "%Y%m%d%H%M%S").replace(tzinfo=KST)


def _partition_messages(
    messages: list[TickMessage],
) -> tuple[list[TickMessage], list[QuarantinedTick]]:
    valid: list[TickMessage] = []
    quarantined: list[QuarantinedTick] = []
    for message in messages:
        try:
            _validate_message(message)
        except (KeyError, TypeError, ValueError) as exc:
            logger.error(
                "poison-pill tick quarantined topic=%s partition=%s offset=%s reason=%s",
                message.topic,
                message.partition,
                message.offset,
                exc,
            )
            quarantined.append(
                QuarantinedTick(
                    payload=message.value,
                    topic=message.topic,
                    partition=message.partition,
                    offset=message.offset,
                    reason=str(exc),
                )
            )
            continue
        valid.append(message)
    return valid, quarantined


def _validate_message(message: TickMessage) -> None:
    tick = message.value
    for field in _REQUIRED_TICK_FIELDS:
        if tick.get(field) is None:
            raise ValueError(f"missing required tick field: {field}")
    if tick.get("market") is None and tick.get("source_tr_id") is None:
        raise ValueError("missing required tick identity field: market/source_tr_id")
    _event_ts(tick)
    int(tick["price"])
    int(tick["trade_volume"])
    int(tick["cumulative_volume"])
    compute_event_id(tick)


def _latest_ticks_by_symbol(inserted_ticks: list[InsertedTick]) -> dict[str, InsertedTick]:
    latest: dict[str, InsertedTick] = {}
    for inserted_tick in inserted_ticks:
        symbol = str(inserted_tick.value["symbol"])
        current = latest.get(symbol)
        if current is None or _inserted_tick_key(inserted_tick) > _inserted_tick_key(current):
            latest[symbol] = inserted_tick
    return latest


def _inserted_tick_key(inserted_tick: InsertedTick) -> tuple[datetime, int, int]:
    return (inserted_tick.event_ts, inserted_tick.partition, inserted_tick.offset)

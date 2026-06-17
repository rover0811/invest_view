from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.aggregation.ohlc import BarState, FiveMinuteAggregator, KST
from tick_persistence.db.models import Symbol5mMetrics, SymbolSnapshot, TickHistory, TickQuarantine
from tick_persistence.handler import TickHandler
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.quarantine import QuarantineRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository

pytestmark = pytest.mark.qa


def _tick_value(*, symbol: str = "005930", price: int, trade_time: str, volume: int = 1) -> dict[str, Any]:
    return {
        "source_tr_id": "H0STCNT0",
        "market": "KRX",
        "received_at": "2026-06-01T00:00:01+00:00",
        "symbol": symbol,
        "business_date": "20260601",
        "trade_time": trade_time,
        "price": price,
        "trade_type": "2",
        "trade_volume": volume,
        "vwap": Decimal(str(price)),
        "change": price - 70000,
        "change_rate": Decimal("1.23"),
        "change_sign": "2",
        "cumulative_volume": 1_000_000 + volume,
        "trade_strength": Decimal("105.50"),
        "vi_trigger_price": 71000,
        "trading_halted": "0",
    }


def _message(value: dict[str, Any], *, offset: int, partition: int = 0) -> TickMessage:
    return TickMessage(value=value, topic="stock-ticks", partition=partition, offset=offset, headers={})


def _handler(
    db_session_factory,
    *,
    snapshot_repo: SnapshotRepository | None = None,
    quarantine_repo: QuarantineRepository | None = None,
) -> TickHandler:
    return TickHandler(
        session_factory=db_session_factory,
        tick_history_repo=TickHistoryRepository(),
        snapshot_repo=snapshot_repo or SnapshotRepository(),
        metrics_repo=Metrics5mRepository(),
        aggregator=FiveMinuteAggregator(),
        quarantine_repo=quarantine_repo,
    )


async def _quarantine_rows(db_session_factory) -> list[TickQuarantine]:
    async with db_session_factory() as session:
        rows = (
            (await session.execute(sa.select(TickQuarantine).order_by(TickQuarantine.kafka_offset)))
            .scalars()
            .all()
        )
    return list(rows)


def test_handler_clear_state_drops_hydration_and_aggregator_state(db_session_factory):
    handler = _handler(db_session_factory)
    bucket = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    bar = BarState.from_tick(price=70000, volume=10, vwap=Decimal("70000"), tick_key=(bucket, 0, 1))

    handler._aggregator.hydrate("005930", bucket, bar)
    handler._hydrated_keys.add(("005930", bucket))

    handler.clear_state()

    assert handler._hydrated_keys == set()
    assert handler._aggregator.has_bar("005930", bucket) is False
    assert handler._aggregator.current_bar("005930") is None


async def _counts(db_session_factory, symbol: str) -> tuple[int, int, int]:
    async with db_session_factory() as session:
        bronze = await session.scalar(sa.select(sa.func.count()).select_from(TickHistory).where(TickHistory.symbol == symbol))
        silver = await session.scalar(
            sa.select(sa.func.count()).select_from(Symbol5mMetrics).where(Symbol5mMetrics.symbol == symbol)
        )
        snapshot = await session.scalar(
            sa.select(sa.func.count()).select_from(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol)
        )
    return int(bronze or 0), int(silver or 0), int(snapshot or 0)


async def _bar_and_snapshot(db_session_factory, symbol: str) -> tuple[Symbol5mMetrics, SymbolSnapshot]:
    async with db_session_factory() as session:
        bar = (await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == symbol))).scalar_one()
        snapshot = (await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))).scalar_one()
    return bar, snapshot


async def _bar_snapshot_state(db_session_factory, symbol: str) -> tuple[object, ...]:
    bar, snapshot = await _bar_and_snapshot(db_session_factory, symbol)
    return (
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
        bar.tick_count,
        bar.is_final,
        snapshot.last_price,
        snapshot.last_trade_time,
        snapshot.last_event_ts,
        snapshot.cumulative_volume,
    )


async def test_handler_persists_bronze_silver_and_snapshot_in_one_bucket(db_session_factory):
    handler = _handler(db_session_factory)
    prices = [70000, 70500, 69800, 70100, 70200]
    trade_times = ["090000", "090100", "090200", "090300", "090400"]

    for offset, (price, trade_time) in enumerate(zip(prices, trade_times, strict=True), start=1):
        await handler.handle(_message(_tick_value(price=price, trade_time=trade_time), offset=offset))

    async with db_session_factory() as session:
        bronze_count = await session.scalar(sa.select(sa.func.count()).select_from(TickHistory))
        bar = (await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == "005930"))).scalar_one()
        snapshot = (await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == "005930"))).scalar_one()

    assert bronze_count == 5
    assert bar.open == 70000
    assert bar.high == 70500
    assert bar.low == 69800
    assert bar.close == 70200
    assert bar.tick_count == 5
    assert bar.is_final is False
    assert snapshot.last_price == 70200


async def test_handler_rolls_back_all_writes_when_snapshot_fails(db_session_factory, monkeypatch):
    symbol = "000660"
    snapshot_repo = SnapshotRepository()

    async def fail_snapshot(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("snapshot boom")

    monkeypatch.setattr(snapshot_repo, "upsert_snapshot", fail_snapshot)
    handler = _handler(db_session_factory, snapshot_repo=snapshot_repo)

    with pytest.raises(RuntimeError, match="snapshot boom"):
        await handler.handle(_message(_tick_value(symbol=symbol, price=70000, trade_time="090000"), offset=100))

    assert await _counts(db_session_factory, symbol) == (0, 0, 0)


async def test_handler_hydrates_live_bucket_after_restart(db_session_factory):
    first_handler = _handler(db_session_factory)
    first_ticks = [
        _tick_value(price=70000, trade_time="090000"),
        _tick_value(price=70500, trade_time="090100"),
        _tick_value(price=70400, trade_time="090200"),
    ]
    for offset, tick in enumerate(first_ticks, start=200):
        await first_handler.handle(_message(tick, offset=offset))

    restarted_handler = _handler(db_session_factory)
    await restarted_handler.handle(_message(_tick_value(price=69900, trade_time="090300"), offset=203))

    async with db_session_factory() as session:
        bar = (await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == "005930"))).scalar_one()

    assert bar.open == 70000
    assert bar.high == 70500
    assert bar.low == 69900
    assert bar.close == 69900
    assert bar.tick_count == 4
    assert bar.is_final is False


async def test_handler_duplicate_kafka_message_does_not_double_count_silver(db_session_factory):
    symbol = "035420"
    handler = _handler(db_session_factory)
    messages = [
        _message(_tick_value(symbol=symbol, price=70000, trade_time="090000", volume=10), offset=0),
        _message(_tick_value(symbol=symbol, price=70100, trade_time="090100", volume=20), offset=1),
        _message(_tick_value(symbol=symbol, price=70200, trade_time="090200", volume=30), offset=2),
    ]
    for message in messages:
        await handler.handle(message)

    before_bar, before_snapshot = await _bar_and_snapshot(db_session_factory, symbol)
    await handler.handle(messages[1])
    after_bar, after_snapshot = await _bar_and_snapshot(db_session_factory, symbol)

    assert await _counts(db_session_factory, symbol) == (3, 1, 1)
    assert before_bar.tick_count == 3
    assert before_bar.volume == 60
    assert after_bar.tick_count == 3
    assert after_bar.volume == 60
    assert after_bar.close == 70200
    assert before_snapshot.last_price == 70200
    assert after_snapshot.last_price == 70200


async def test_handle_batch_counts_only_inserted_rows_by_partition_offset(db_session_factory):
    symbol = "207940"
    handler = _handler(db_session_factory)
    first = _tick_value(symbol=symbol, price=70000, trade_time="090000", volume=10)
    duplicate_same_event_id = dict(first)
    later = _tick_value(symbol=symbol, price=70200, trade_time="090100", volume=30)

    await handler.handle_batch(
        [
            _message(first, partition=1, offset=10),
            _message(duplicate_same_event_id, partition=1, offset=11),
            _message(later, partition=1, offset=12),
        ]
    )

    bar, snapshot = await _bar_and_snapshot(db_session_factory, symbol)
    assert await _counts(db_session_factory, symbol) == (2, 1, 1)
    assert bar.tick_count == 2
    assert bar.volume == 40
    assert bar.close == 70200
    assert snapshot.last_price == 70200


async def test_handle_batch_replay_is_idempotent_after_cold_restart(db_session_factory):
    symbol = "032830"
    messages = [
        _message(_tick_value(symbol=symbol, price=70000, trade_time="090000", volume=10), offset=20),
        _message(_tick_value(symbol=symbol, price=70100, trade_time="090100", volume=20), offset=21),
        _message(_tick_value(symbol=symbol, price=70200, trade_time="090200", volume=30), offset=22),
    ]

    first_handler = _handler(db_session_factory)
    await first_handler.handle_batch(messages)
    before = await _bar_snapshot_state(db_session_factory, symbol)

    restarted_handler = _handler(db_session_factory)
    await restarted_handler.handle_batch(messages)
    after = await _bar_snapshot_state(db_session_factory, symbol)

    assert await _counts(db_session_factory, symbol) == (3, 1, 1)
    assert before == after


async def test_handle_batch_rolls_back_all_writes_when_snapshot_fails(db_session_factory, monkeypatch):
    symbol = "086790"
    snapshot_repo = SnapshotRepository()

    async def fail_snapshot(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("batch snapshot boom")

    monkeypatch.setattr(snapshot_repo, "upsert_snapshot", fail_snapshot)
    handler = _handler(db_session_factory, snapshot_repo=snapshot_repo)

    with pytest.raises(RuntimeError, match="batch snapshot boom"):
        await handler.handle_batch(
            [
                _message(_tick_value(symbol=symbol, price=70000, trade_time="090000"), offset=30),
                _message(_tick_value(symbol=symbol, price=70100, trade_time="090100"), offset=31),
            ]
        )

    assert await _counts(db_session_factory, symbol) == (0, 0, 0)


async def test_handle_batch_upserts_one_latest_snapshot_per_symbol(db_session_factory):
    symbol = "011200"
    calls: list[Mapping[str, object]] = []

    class CountingSnapshotRepository(SnapshotRepository):
        async def upsert_snapshot(self, session: AsyncSession, tick: Mapping[str, object]) -> None:
            calls.append(tick)
            await super().upsert_snapshot(session, tick)

    handler = _handler(db_session_factory, snapshot_repo=CountingSnapshotRepository())
    await handler.handle_batch(
        [
            _message(_tick_value(symbol=symbol, price=70200, trade_time="090200"), offset=40),
            _message(_tick_value(symbol=symbol, price=70000, trade_time="090000"), offset=41),
            _message(_tick_value(symbol=symbol, price=70100, trade_time="090100"), offset=42),
        ]
    )

    _, snapshot = await _bar_and_snapshot(db_session_factory, symbol)
    assert [tick["trade_time"] for tick in calls] == ["090200"]
    assert snapshot.last_price == 70200
    assert snapshot.last_trade_time == "090200"


async def test_handle_batch_quarantines_invalid_ticks_and_keeps_processing_valid(db_session_factory):
    symbol = "251270"
    quarantine_repo = QuarantineRepository()
    handler = _handler(db_session_factory, quarantine_repo=quarantine_repo)
    missing_field = _tick_value(symbol=symbol, price=70000, trade_time="090000")
    del missing_field["symbol"]
    empty_field = _tick_value(symbol="", price=70000, trade_time="090000")

    await handler.handle_batch(
        [
            _message(missing_field, partition=2, offset=50),
            _message(empty_field, partition=2, offset=51),
            _message(_tick_value(symbol=symbol, price=70100, trade_time="090100"), partition=2, offset=52),
        ]
    )

    bar, snapshot = await _bar_and_snapshot(db_session_factory, symbol)
    assert await _counts(db_session_factory, symbol) == (1, 1, 1)
    assert bar.tick_count == 1
    assert snapshot.last_price == 70100

    rows = await _quarantine_rows(db_session_factory)
    assert [(row.kafka_partition, row.kafka_offset) for row in rows] == [(2, 50), (2, 51)]
    assert all(row.kafka_topic == "stock-ticks" for row in rows)
    assert "missing required tick field: symbol" in rows[0].reason
    assert "empty tick identity field: symbol" in rows[1].reason
    assert rows[0].raw_payload["price"] == 70000
    assert rows[1].raw_payload["symbol"] == ""
    assert rows[0].quarantined_at is not None
    assert quarantine_repo.quarantined_count == 2
    assert handler.quarantined_count == 2


async def test_handle_batch_quarantine_only_batch_commits_and_progresses(db_session_factory):
    handler = _handler(db_session_factory)
    invalid = _tick_value(symbol="900001", price=70000, trade_time="090000")
    del invalid["trade_type"]

    await handler.handle_batch([_message(invalid, partition=3, offset=70)])

    rows = await _quarantine_rows(db_session_factory)
    assert [(row.kafka_partition, row.kafka_offset) for row in rows] == [(3, 70)]
    assert handler.quarantined_count == 1


async def test_handle_batch_quarantine_is_idempotent_on_replay(db_session_factory):
    invalid = _tick_value(symbol="900002", price=70000, trade_time="090000")
    del invalid["business_date"]
    message = _message(invalid, partition=4, offset=80)

    first_handler = _handler(db_session_factory)
    await first_handler.handle_batch([message])

    restarted_handler = _handler(db_session_factory)
    await restarted_handler.handle_batch([message])

    rows = await _quarantine_rows(db_session_factory)
    assert len([row for row in rows if (row.kafka_partition, row.kafka_offset) == (4, 80)]) == 1
    assert restarted_handler.quarantined_count == 0


async def test_handle_batch_rolls_back_quarantine_with_failed_valid_batch(db_session_factory, monkeypatch):
    symbol = "900003"
    snapshot_repo = SnapshotRepository()

    async def fail_snapshot(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("rollback boom")

    monkeypatch.setattr(snapshot_repo, "upsert_snapshot", fail_snapshot)
    handler = _handler(db_session_factory, snapshot_repo=snapshot_repo)
    invalid = _tick_value(symbol=symbol, price=70000, trade_time="090000")
    del invalid["symbol"]

    with pytest.raises(RuntimeError, match="rollback boom"):
        await handler.handle_batch(
            [
                _message(invalid, partition=5, offset=90),
                _message(_tick_value(symbol=symbol, price=70100, trade_time="090100"), partition=5, offset=91),
            ]
        )

    assert await _quarantine_rows(db_session_factory) == []


async def test_handler_restart_then_duplicate_replay_does_not_hydrate_and_double_count(db_session_factory):
    symbol = "068270"
    first_handler = _handler(db_session_factory)
    messages = [
        _message(_tick_value(symbol=symbol, price=70000, trade_time="090000", volume=10), offset=0),
        _message(_tick_value(symbol=symbol, price=70100, trade_time="090100", volume=20), offset=1),
        _message(_tick_value(symbol=symbol, price=70200, trade_time="090200", volume=30), offset=2),
    ]
    for message in messages:
        await first_handler.handle(message)

    restarted_handler = _handler(db_session_factory)
    await restarted_handler.handle(messages[2])

    bar, snapshot = await _bar_and_snapshot(db_session_factory, symbol)
    assert await _counts(db_session_factory, symbol) == (3, 1, 1)
    assert bar.tick_count == 3
    assert bar.volume == 60
    assert bar.close == 70200
    assert snapshot.last_price == 70200


async def test_handler_bounds_aggregator_and_hydrated_keys_across_many_buckets(db_session_factory):
    handler = _handler(db_session_factory)
    trade_times = ["090000", "090500", "091000", "091500", "092000", "092500", "093000", "093500"]

    for offset, trade_time in enumerate(trade_times, start=500):
        await handler.handle(_message(_tick_value(price=70000 + offset, trade_time=trade_time), offset=offset))
        assert len(handler._aggregator._bars) <= 1
        assert len(handler._hydrated_keys) <= 1

    async with db_session_factory() as session:
        bars = (
            (
                await session.execute(
                    sa.select(Symbol5mMetrics)
                    .where(Symbol5mMetrics.symbol == "005930")
                    .order_by(Symbol5mMetrics.bucket_start)
                )
            )
            .scalars()
            .all()
        )

    assert len(bars) == len(trade_times)
    assert [bar.is_final for bar in bars] == [True, True, True, True, True, True, True, False]
    assert [bar.open for bar in bars] == [70500, 70501, 70502, 70503, 70504, 70505, 70506, 70507]
    assert handler._hydrated_keys == {("005930", datetime(2026, 6, 1, 9, 35, tzinfo=KST))}

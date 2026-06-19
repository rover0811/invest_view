"""Repository QA tests against a real testcontainers Postgres migrated via alembic upgrade head."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from tests._rtt_harness import count_db_roundtrips
from tick_persistence.aggregation.ohlc import BarState
from tick_persistence.db.models import Symbol5mMetrics, SymbolSnapshot, TickHistory
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository import tick_history as tick_history_module
from tick_persistence.repository.tick_history import TickHistoryRepository

pytestmark = pytest.mark.qa

KST = ZoneInfo("Asia/Seoul")


def _tick_value(symbol: str = "005930", price: int = 70000, volume: int = 1500) -> dict[str, Any]:
    return {
        "source_tr_id": "H0STCNT0",
        "market": "KRX",
        "received_at": "2026-06-01T00:00:01+00:00",
        "symbol": symbol,
        "price": price,
        "trade_volume": volume,
        "vwap": Decimal("70123.45"),
        "change": 250,
        "change_rate": Decimal("1.23"),
        "change_sign": "2",
        "cumulative_volume": 9_876_543,
        "trade_strength": Decimal("105.50"),
        "vi_trigger_price": 71000,
        "trading_halted": "0",
        "trade_time": "090301",
        "trade_type": "2",
        "business_date": "20260601",
    }


def _message(value: dict[str, Any], *, topic: str = "stock-ticks", partition: int = 0, offset: int = 123) -> TickMessage:
    return TickMessage(value=value, topic=topic, partition=partition, offset=offset, headers={})


def _hhmmss(seconds_after_midnight: int) -> str:
    hours, remainder = divmod(seconds_after_midnight, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}{minutes:02}{seconds:02}"


async def _insert_tick(db_session_factory, repo: TickHistoryRepository, message: TickMessage) -> bool:
    async with db_session_factory() as session:
        inserted = await repo.insert(session, message)
        await session.commit()
        return inserted


async def test_bronze_insert_round_trip(db_session_factory):
    repo = TickHistoryRepository()
    message = _message(_tick_value(), partition=2, offset=456)

    inserted = await _insert_tick(db_session_factory, repo, message)
    assert inserted is True

    async with db_session_factory() as session:
        row = (
            await session.execute(sa.select(TickHistory).where(TickHistory.symbol == "005930"))
        ).scalar_one()

    assert row.symbol == "005930"
    assert row.price == 70000
    assert isinstance(row.price, int)
    assert row.trade_volume == 1500
    assert isinstance(row.trade_volume, int)
    assert row.vwap == Decimal("70123.45")
    assert isinstance(row.vwap, Decimal)
    assert row.change_rate == Decimal("1.23")
    assert row.kafka_topic == "stock-ticks"
    assert row.kafka_partition == 2
    assert row.kafka_offset == 456
    assert row.tick_dedupe_key is not None
    assert row.event_id == "d73d1d9a-3c9d-5411-a711-33ed3789bf23"
    assert row.event_ts == datetime(2026, 6, 1, 9, 3, 1, tzinfo=KST)


async def test_bronze_same_event_id_at_different_offsets_is_skipped(db_session_factory):
    repo = TickHistoryRepository()
    tick = _tick_value(price=70000)
    message = _message(tick, topic="stock-ticks", partition=1, offset=999)

    first = await _insert_tick(db_session_factory, repo, message)
    second = await _insert_tick(db_session_factory, repo, _message(tick, topic="stock-ticks", partition=2, offset=1000))

    assert first is True
    assert second is False

    async with db_session_factory() as session:
        count = await session.scalar(
            sa.text("SELECT count(*) FROM bronze.tick_history WHERE event_id = :event_id"),
            {"event_id": "d73d1d9a-3c9d-5411-a711-33ed3789bf23"},
        )
    assert count == 1


async def test_bronze_prefers_payload_event_id_when_present(db_session_factory):
    repo = TickHistoryRepository()
    payload_event_id = "cc293f67-5c08-58c8-86fb-ef8835363c9c"
    tick = {
        **_tick_value(symbol="005930", price=70100),
        "business_date": "20260617",
        "trade_time": "091530",
        "cumulative_volume": 123456,
        "event_id": payload_event_id,
    }

    assert await _insert_tick(db_session_factory, repo, _message(tick, partition=3, offset=1001)) is True

    async with db_session_factory() as session:
        event_id = await session.scalar(sa.select(TickHistory.event_id).where(TickHistory.kafka_offset == 1001))

    assert event_id == payload_event_id


async def test_bronze_insert_many_splits_large_batch_and_returns_inserted_lineage(db_session_factory):
    repo = TickHistoryRepository()
    max_rows = tick_history_module.postgres_max_insert_rows()
    messages = [
        _message(
            _tick_value(price=70_000 + (index % 100), volume=1)
            | {
                "trade_time": _hhmmss(9 * 3600 + index),
                "cumulative_volume": 1_000_000 + index,
            },
            partition=2,
            offset=index,
        )
        for index in range(max_rows + 1)
    ]

    async with db_session_factory() as session:
        inserted = await repo.insert_many(session, messages)
        await session.commit()

    async with db_session_factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(TickHistory))

    assert len(inserted) == len(messages)
    assert int(count or 0) == len(messages)
    assert {(row.partition, row.offset) for row in inserted} == {(2, index) for index in range(max_rows + 1)}
    assert all(row.value is messages[row.offset].value for row in inserted)
    assert all(row.market == "KRX" for row in inserted)


def _bar(*, close: int, is_final: bool, tick_count: int) -> BarState:
    return BarState(
        open=70000,
        high=70500,
        low=69800,
        close=close,
        volume=1500,
        vwap_last=Decimal("70200.50"),
        tick_count=tick_count,
        open_key=(datetime(2026, 6, 1, 9, 0, tzinfo=KST), 0, 1),
        close_key=(datetime(2026, 6, 1, 9, 4, tzinfo=KST), 0, 5),
        is_final=is_final,
    )


async def test_silver_upsert_is_idempotent_and_updates(db_session_factory):
    repo = Metrics5mRepository()
    symbol = "005930"
    bucket_start = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    bucket_end = datetime(2026, 6, 1, 9, 5, tzinfo=KST)

    async with db_session_factory() as session:
        await repo.upsert_bar(session, symbol, bucket_start, bucket_end, _bar(close=70100, is_final=False, tick_count=4))
        await session.commit()
    async with db_session_factory() as session:
        await repo.upsert_bar(session, symbol, bucket_start, bucket_end, _bar(close=70300, is_final=True, tick_count=5))
        await session.commit()

    async with db_session_factory() as session:
        rows = (
            await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == symbol))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].close == 70300
    assert rows[0].is_final is True
    assert rows[0].tick_count == 5

    async with db_session_factory() as session:
        loaded = await repo.load_bar_state(session, symbol, bucket_start)
    assert loaded is None


async def test_silver_bulk_upsert_uses_one_statement_for_forty_one_bars(db_session_factory):
    repo = Metrics5mRepository()
    bucket_start = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    bucket_end = datetime(2026, 6, 1, 9, 5, tzinfo=KST)
    bars: list[tuple[str, datetime, datetime | None, BarState]] = [
        (f"{index:06}", bucket_start, bucket_end, _bar(close=70_000 + index, is_final=False, tick_count=index + 1))
        for index in range(41)
    ]
    engine = db_session_factory.kw["bind"]

    async with db_session_factory() as session:
        with count_db_roundtrips(engine) as ctr:
            await repo.upsert_bars(session, bars)
        await session.commit()

    async with db_session_factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(Symbol5mMetrics))

    assert ctr.statements == 1
    assert ctr.inserts == 1
    assert int(count or 0) == 41


async def test_silver_bulk_upsert_is_idempotent_and_updates_existing_row(db_session_factory):
    repo = Metrics5mRepository()
    symbol = "005930"
    bucket_start = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    bucket_end = datetime(2026, 6, 1, 9, 5, tzinfo=KST)

    async with db_session_factory() as session:
        await repo.upsert_bars(session, [(symbol, bucket_start, bucket_end, _bar(close=70100, is_final=False, tick_count=4))])
        await session.commit()
    async with db_session_factory() as session:
        await repo.upsert_bars(session, [(symbol, bucket_start, bucket_end, _bar(close=70300, is_final=True, tick_count=5))])
        await session.commit()

    async with db_session_factory() as session:
        rows = (
            await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == symbol))
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].close == 70300
    assert rows[0].is_final is True
    assert rows[0].tick_count == 5


async def test_silver_bulk_upsert_deduplicates_same_symbol_bucket_before_conflict(db_session_factory):
    repo = Metrics5mRepository()
    symbol = "005930"
    bucket_start = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    bucket_end = datetime(2026, 6, 1, 9, 5, tzinfo=KST)
    bars: list[tuple[str, datetime, datetime | None, BarState]] = [
        (symbol, bucket_start, bucket_end, _bar(close=70100, is_final=False, tick_count=4)),
        (symbol, bucket_start, bucket_end, _bar(close=70200, is_final=False, tick_count=5)),
        (symbol, bucket_start, bucket_end, _bar(close=70300, is_final=True, tick_count=6)),
    ]

    async with db_session_factory() as session:
        await repo.upsert_bars(session, bars)
        await session.commit()

    async with db_session_factory() as session:
        rows = (
            await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == symbol))
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].close == 70300
    assert rows[0].is_final is True
    assert rows[0].tick_count == 6


async def test_silver_bulk_upsert_empty_list_does_not_execute(db_session_factory):
    repo = Metrics5mRepository()
    engine = db_session_factory.kw["bind"]

    async with db_session_factory() as session:
        with count_db_roundtrips(engine) as ctr:
            await repo.upsert_bars(session, [])

    assert ctr.statements == 0


async def test_load_bar_state_reconstructs_active_bucket_from_bronze_order_keys(db_session_factory):
    tick_repo = TickHistoryRepository()
    repo = Metrics5mRepository()
    symbol = "005930"
    bucket_start = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    ticks = [
        (_tick_value(symbol=symbol, price=70100, volume=13) | {"trade_time": "090400", "vwap": Decimal("70100.00")}, 2, 30),
        (_tick_value(symbol=symbol, price=70500, volume=7) | {"trade_time": "090100", "vwap": Decimal("70500.00")}, 0, 1),
        (_tick_value(symbol=symbol, price=69800, volume=11) | {"trade_time": "090300", "vwap": Decimal("69800.00")}, 1, 20),
    ]

    async with db_session_factory() as session:
        for tick, partition, offset in ticks:
            await tick_repo.insert(session, _message(tick, partition=partition, offset=offset))
        await session.commit()

    async with db_session_factory() as session:
        loaded = await repo.load_bar_state(session, symbol, bucket_start)

    assert loaded is not None
    assert loaded.open == 70500
    assert loaded.high == 70500
    assert loaded.low == 69800
    assert loaded.close == 70100
    assert loaded.volume == 31
    assert loaded.tick_count == 3
    assert loaded.vwap_last == Decimal("70100.00")
    assert loaded.open_key == (datetime(2026, 6, 1, 9, 1, tzinfo=KST), 0, 1)
    assert loaded.close_key == (datetime(2026, 6, 1, 9, 4, tzinfo=KST), 2, 30)


async def test_silver_load_bar_state_absent_returns_none(db_session_factory):
    repo = Metrics5mRepository()
    async with db_session_factory() as session:
        loaded = await repo.load_bar_state(session, "000000", datetime(2026, 6, 1, 9, 0, tzinfo=KST))
    assert loaded is None


async def test_snapshot_equal_event_ts_applies_last_write(db_session_factory):
    repo = SnapshotRepository()
    symbol = "005930"

    async with db_session_factory() as session:
        await repo.upsert_snapshot(session, _tick_value(symbol=symbol, price=70000))
        await session.commit()
    async with db_session_factory() as session:
        await repo.upsert_snapshot(session, _tick_value(symbol=symbol, price=70500))
        await session.commit()

    async with db_session_factory() as session:
        rows = (
            await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].last_price == 70500
    assert rows[0].last_trade_time == "090301"
    assert rows[0].cumulative_volume == 9_876_543


async def test_snapshot_bulk_upsert_uses_one_statement_for_forty_one_symbols(db_session_factory):
    repo = SnapshotRepository()
    ticks: list[Mapping[str, object]] = [
        _tick_value(symbol=f"{index:06}", price=70_000 + index)
        | {
            "trade_time": _hhmmss(9 * 3600 + index),
            "cumulative_volume": 1_000_000 + index,
        }
        for index in range(41)
    ]
    engine = db_session_factory.kw["bind"]

    async with db_session_factory() as session:
        with count_db_roundtrips(engine) as ctr:
            await repo.upsert_snapshots(session, ticks)
        await session.commit()

    async with db_session_factory() as session:
        count = await session.scalar(sa.select(sa.func.count()).select_from(SymbolSnapshot))

    assert ctr.statements == 1
    assert ctr.inserts == 1
    assert int(count or 0) == 41


async def test_snapshot_bulk_upsert_deduplicates_same_symbol_before_conflict(db_session_factory):
    repo = SnapshotRepository()
    symbol = "005930"
    ticks: list[Mapping[str, object]] = [
        _tick_value(symbol=symbol, price=70_000) | {"trade_time": "090301"},
        _tick_value(symbol=symbol, price=70_500) | {"trade_time": "090302"},
        _tick_value(symbol=symbol, price=70_700) | {"trade_time": "090302"},
    ]

    async with db_session_factory() as session:
        await repo.upsert_snapshots(session, ticks)
        await session.commit()

    async with db_session_factory() as session:
        rows = (
            await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].last_price == 70_700
    assert rows[0].last_trade_time == "090302"


async def test_snapshot_bulk_upsert_keeps_newer_existing_row(db_session_factory):
    repo = SnapshotRepository()
    symbol = "005930"
    newer_tick = _tick_value(symbol=symbol, price=71_000) | {"trade_time": "090500"}
    older_ticks: list[Mapping[str, object]] = [
        _tick_value(symbol=symbol, price=70_000) | {"trade_time": "090301"}
    ]

    async with db_session_factory() as session:
        await repo.upsert_snapshot(session, newer_tick)
        await session.commit()

    async with db_session_factory() as session:
        await repo.upsert_snapshots(session, older_ticks)
        await session.commit()

    async with db_session_factory() as session:
        row = (
            await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))
        ).scalar_one()

    assert row.last_price == 71_000
    assert row.last_trade_time == "090500"


async def test_snapshot_bulk_upsert_empty_list_does_not_execute(db_session_factory):
    repo = SnapshotRepository()
    engine = db_session_factory.kw["bind"]

    async with db_session_factory() as session:
        with count_db_roundtrips(engine) as ctr:
            await repo.upsert_snapshots(session, [])

    assert ctr.statements == 0

"""Repository QA tests against a real testcontainers Postgres migrated via alembic upgrade head."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa

from tick_persistence.aggregation.ohlc import BarState
from tick_persistence.db.models import Symbol5mMetrics, SymbolSnapshot, TickHistory
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.snapshot import SnapshotRepository
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


def _bar(*, close: int, is_final: bool, tick_count: int) -> BarState:
    return BarState(
        open=70000,
        high=70500,
        low=69800,
        close=close,
        volume=1500,
        vwap_last=Decimal("70200.50"),
        tick_count=tick_count,
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
    assert loaded is not None
    assert loaded.close == 70300
    assert loaded.open == 70000
    assert loaded.high == 70500
    assert loaded.low == 69800
    assert loaded.tick_count == 5
    assert loaded.is_final is True
    assert loaded.vwap_last == Decimal("70200.50")


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

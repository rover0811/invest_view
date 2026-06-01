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
    assert row.tick_dedupe_key == "stock-ticks:2:456"


async def test_bronze_duplicate_message_is_skipped(db_session_factory):
    repo = TickHistoryRepository()
    message = _message(_tick_value(price=70000), topic="stock-ticks", partition=1, offset=999)

    first = await _insert_tick(db_session_factory, repo, message)
    second = await _insert_tick(db_session_factory, repo, _message(_tick_value(price=70500), topic="stock-ticks", partition=1, offset=999))

    assert first is True
    assert second is False

    async with db_session_factory() as session:
        count = await session.scalar(
            sa.text("SELECT count(*) FROM bronze.tick_history WHERE tick_dedupe_key = :k"),
            {"k": "stock-ticks:1:999"},
        )
    assert count == 1


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


async def test_snapshot_upsert_keeps_latest(db_session_factory):
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

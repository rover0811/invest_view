from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa

from tick_persistence.aggregation.ohlc import FiveMinuteAggregator
from tick_persistence.db.models import Symbol5mMetrics, SymbolSnapshot, TickHistory
from tick_persistence.handler import TickHandler
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository

pytestmark = pytest.mark.qa


def _tick_value(*, symbol: str = "005930", price: int, trade_time: str, volume: int = 1) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "business_date": "20260601",
        "trade_time": trade_time,
        "price": price,
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


def _message(value: dict[str, Any], *, offset: int) -> TickMessage:
    return TickMessage(value=value, topic="stock-ticks", partition=0, offset=offset, headers={})


def _handler(db_session_factory, *, snapshot_repo: SnapshotRepository | None = None) -> TickHandler:
    return TickHandler(
        session_factory=db_session_factory,
        tick_history_repo=TickHistoryRepository(),
        snapshot_repo=snapshot_repo or SnapshotRepository(),
        metrics_repo=Metrics5mRepository(),
        aggregator=FiveMinuteAggregator(),
    )


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

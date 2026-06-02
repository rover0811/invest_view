from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def _tick_value(
    *, symbol: str = "005930", price: int, trade_time: str, business_date: str = "20260601", volume: int = 10
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "business_date": business_date,
        "trade_time": trade_time,
        "price": price,
        "trade_volume": volume,
        "vwap": Decimal(str(price)),
        "change": price - 70000,
        "change_rate": Decimal("1.23"),
        "change_sign": "2",
        "cumulative_volume": 1_000_000 + volume,
        "trade_strength": Decimal("100.0"),
        "vi_trigger_price": 71000,
        "trading_halted": "N",
    }


def _message(value: dict[str, Any], *, offset: int) -> TickMessage:
    return TickMessage(value=value, topic="stock-ticks", partition=0, offset=offset, headers={})


@pytest.mark.asyncio
async def test_tick_persistence_e2e_data_path(db_session_factory):
    """
    Deterministic E2E test for tick_persistence data path.
    Verifies:
    1. Bronze: raw tick storage.
    2. Silver: 5m OHLC aggregation with bucket transitions (is_final).
    3. Serving: symbol snapshot updates.
    4. Serving: signal timeline view (alert + pattern union).

    Note: Indicator logic (RSI/MACD) is covered by Java unit tests (T16).
    This test focuses on the Python persistence and view layer.
    """
    handler = TickHandler(
        session_factory=db_session_factory,
        tick_history_repo=TickHistoryRepository(),
        snapshot_repo=SnapshotRepository(),
        metrics_repo=Metrics5mRepository(),
        aggregator=FiveMinuteAggregator(),
    )

    symbol = "005930"

    # 1. First bucket (09:00 - 09:05)
    ticks_b1 = [
        (70000, "090001"),
        (70500, "090200"),
        (69800, "090459"),
    ]
    for i, (price, ttime) in enumerate(ticks_b1):
        await handler.handle(_message(_tick_value(symbol=symbol, price=price, trade_time=ttime), offset=i))

    # 2. Second bucket (09:05 - 09:10) - triggers first bucket finalization
    ticks_b2 = [
        (71000, "090500"),
        (70800, "090700"),
    ]
    for i, (price, ttime) in enumerate(ticks_b2, start=len(ticks_b1)):
        await handler.handle(_message(_tick_value(symbol=symbol, price=price, trade_time=ttime), offset=i))

    # Verify DB state
    async with db_session_factory() as session:
        # Bronze
        bronze_count = await session.scalar(sa.select(sa.func.count()).select_from(TickHistory))
        assert bronze_count == 5

        # Silver
        bars = (
            (
                await session.execute(
                    sa.select(Symbol5mMetrics)
                    .where(Symbol5mMetrics.symbol == symbol)
                    .order_by(Symbol5mMetrics.bucket_start)
                )
            )
            .scalars()
            .all()
        )

        assert len(bars) == 2

        # Bar 1 (09:00)
        assert bars[0].open == 70000
        assert bars[0].high == 70500
        assert bars[0].low == 69800
        assert bars[0].close == 69800
        assert bars[0].tick_count == 3
        assert bars[0].is_final is True

        # Bar 2 (09:05)
        assert bars[1].open == 71000
        assert bars[1].high == 71000
        assert bars[1].low == 70800
        assert bars[1].close == 70800
        assert bars[1].tick_count == 2
        assert bars[1].is_final is False

        # Serving Snapshot
        snapshot = (
            await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))
        ).scalar_one()
        assert snapshot.last_price == 70800

        # 3. Signal Timeline View
        # Insert deterministic alert and pattern into stubs
        now = datetime.now(timezone.utc)
        await session.execute(
            sa.text(
                "INSERT INTO alert_service.alert_events (symbol, alert_type, triggered_at, trigger_values, severity) "
                "VALUES (:symbol, :type, :at, :vals, :sev)"
            ),
            {
                "symbol": symbol,
                "type": "PRICE_ALERT",
                "at": now - timedelta(minutes=2),
                "vals": '{"price": 70500}',
                "sev": "INFO",
            },
        )

        await session.execute(
            sa.text(
                "INSERT INTO gold.pattern_events (symbol, pattern_type, triggered_at, trigger_values) "
                "VALUES (:symbol, :type, :at, :vals)"
            ),
            {
                "symbol": symbol,
                "type": "GOLDEN_CROSS",
                "at": now - timedelta(minutes=1),
                "vals": '{"ma5": 70200, "ma20": 70100}',
            },
        )

        await session.commit()

        # Query the view
        timeline = (
            await session.execute(
                sa.text(
                    "SELECT event_kind, event_type, triggered_at FROM serving.symbol_signal_timeline "
                    "WHERE symbol = :symbol ORDER BY triggered_at ASC"
                ),
                {"symbol": symbol},
            )
        ).all()

        assert len(timeline) == 2
        assert timeline[0].event_kind == "alert"
        assert timeline[0].event_type == "PRICE_ALERT"
        assert timeline[1].event_kind == "pattern"
        assert timeline[1].event_type == "GOLDEN_CROSS"

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from itertools import accumulate
from typing import Any

import pytest_asyncio
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession

COMMON_HYPOTHESIS_SETTINGS = settings(
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


def _yyyymmdd(day: date) -> str:
    return day.strftime("%Y%m%d")


def _hhmmss(seconds_after_midnight: int) -> str:
    hours, remainder = divmod(seconds_after_midnight, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}{minutes:02}{seconds:02}"


def _symbol_strategy() -> st.SearchStrategy[str]:
    return st.integers(min_value=0, max_value=999_999).map(lambda value: f"{value:06}")


def _decimal_strategy(min_value: str, max_value: str, places: int) -> st.SearchStrategy[Decimal]:
    return st.decimals(
        min_value=Decimal(min_value),
        max_value=Decimal(max_value),
        places=places,
        allow_nan=False,
        allow_infinity=False,
    )


def _tick(
    *,
    symbol: str,
    business_date: str,
    trade_time: str,
    price: int,
    trade_volume: int,
    cumulative_volume: int,
    vwap: Decimal,
    change_rate: Decimal,
    trade_strength: Decimal,
) -> dict[str, Any]:
    return {
        "source_tr_id": "H0STCNT0",
        "market": "KRX",
        "symbol": symbol,
        "price": price,
        "trade_volume": trade_volume,
        "vwap": vwap,
        "change": price - 70_000,
        "change_rate": change_rate,
        "change_sign": "2" if price >= 70_000 else "5",
        "cumulative_volume": cumulative_volume,
        "trade_strength": trade_strength,
        "vi_trigger_price": price + 1_000,
        "trading_halted": "0",
        "trade_time": trade_time,
        "trade_type": "2",
        "business_date": business_date,
    }


@st.composite
def stock_tick(draw: Any, symbol: str | None = None) -> dict[str, Any]:
    tick_symbol = symbol or draw(_symbol_strategy())
    business_day = draw(st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31)))
    seconds_after_midnight = draw(st.integers(min_value=0, max_value=86_399))
    price = draw(st.integers(min_value=1_000, max_value=1_000_000))
    trade_volume = draw(st.integers(min_value=1, max_value=1_000_000))
    cumulative_volume = draw(st.integers(min_value=trade_volume, max_value=10_000_000_000))

    return _tick(
        symbol=tick_symbol,
        business_date=_yyyymmdd(business_day),
        trade_time=_hhmmss(seconds_after_midnight),
        price=price,
        trade_volume=trade_volume,
        cumulative_volume=cumulative_volume,
        vwap=draw(_decimal_strategy("1.00", "1000000.00", 2)),
        change_rate=draw(_decimal_strategy("-30.00000000", "30.00000000", 8)),
        trade_strength=draw(_decimal_strategy("0.00000000", "500.00000000", 8)),
    )


@st.composite
def out_of_order_ticks(draw: Any) -> list[dict[str, Any]]:
    symbol = draw(_symbol_strategy())
    business_day = draw(st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 30)))
    size = draw(st.integers(min_value=2, max_value=8))
    start_second = draw(st.integers(min_value=0, max_value=86_399 - size))
    event_seconds = [start_second + index for index in range(size)]
    volume_deltas = draw(st.lists(st.integers(min_value=1, max_value=1_000_000), min_size=size, max_size=size))
    cumulative_volumes = list(accumulate(volume_deltas))
    prices = draw(st.lists(st.integers(min_value=1_000, max_value=1_000_000), min_size=size, max_size=size))
    ordered_ticks = [
        _tick(
            symbol=symbol,
            business_date=_yyyymmdd(business_day + timedelta(days=second // 86_400)),
            trade_time=_hhmmss(second % 86_400),
            price=price,
            trade_volume=delta,
            cumulative_volume=cumulative_volume,
            vwap=Decimal(price),
            change_rate=Decimal("1.23000000"),
            trade_strength=Decimal("105.50000000"),
        )
        for second, delta, cumulative_volume, price in zip(
            event_seconds, volume_deltas, cumulative_volumes, prices, strict=True
        )
    ]
    identity = tuple(range(size))
    permutation = draw(st.permutations(identity).filter(lambda order: tuple(order) != identity))
    return [ordered_ticks[index] for index in permutation]


@pytest_asyncio.fixture(scope="function")
async def hypothesis_db_session(migrated_url: str) -> AsyncIterator[AsyncSession]:
    from tick_persistence.db.session import create_engine

    engine = create_engine(migrated_url)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()

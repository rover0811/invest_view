"""KRX trading calendar derived from stored KOSPI daily rows.

A date is a trading day when ``reference.index_value`` holds a ``KIS_DAILY``
row for KOSPI (``0001``) on that business date. The market data job writes
those rows after close, so the calendar is only as current as the last run:
dates after the latest stored KOSPI row are unknown, not holidays.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_signal.market_data.repository import (
    DAILY_SOURCE,
    MARKET_INDEX_TICKER,
    daily_observed_at,
)

Executor = AsyncConnection | AsyncSession

_KOSPI_DAYS = """
    SELECT v.business_date
    FROM reference.index_value v
    JOIN reference.entity_index i ON i.entity_id = v.index_entity_id
    WHERE i.index_kind = 'DOMESTIC_INDEX' AND i.ticker = :ticker
      AND v.source = :source
"""


def _params(**extra: object) -> dict[str, object]:
    return {"ticker": MARKET_INDEX_TICKER, "source": DAILY_SOURCE, **extra}


async def is_trading_day(executor: Executor, day: date) -> bool:
    """True when KOSPI has a stored daily row on ``day``."""
    result = await executor.execute(
        text(f"SELECT EXISTS ({_KOSPI_DAYS} AND v.business_date = :day)"),
        _params(day=day),
    )
    return bool(result.scalar())


async def previous_trading_day(executor: Executor, day: date) -> date | None:
    """Latest trading day strictly before ``day``, or None if none is stored."""
    result = await executor.execute(
        text(
            f"SELECT max(business_date) FROM ({_KOSPI_DAYS} "
            "AND v.business_date < :day) AS d"
        ),
        _params(day=day),
    )
    value = result.scalar()
    return value if isinstance(value, date) else None


async def trading_days(executor: Executor, start: date, end: date) -> list[date]:
    """Trading days in ``[start, end]``, ascending."""
    result = await executor.execute(
        text(
            f"{_KOSPI_DAYS} AND v.business_date BETWEEN :start AND :end "
            "ORDER BY v.business_date"
        ),
        _params(start=start, end=end),
    )
    return [row[0] for row in result.all()]


async def previous_close_at(executor: Executor, day: date) -> datetime | None:
    """15:30 KST close of the trading day before ``day`` (DAILY range start)."""
    previous = await previous_trading_day(executor, day)
    return daily_observed_at(previous) if previous is not None else None

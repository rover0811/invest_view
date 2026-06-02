"""Public read-only stock listing API over serving/reference (raw ``text()`` SQL).

INNER JOIN ``serving.symbol_snapshot`` against ``reference.bronze_market_ticker``:
snapshot symbols without a matching reference ticker are excluded. Ordered by
cumulative volume (most-traded first).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


router = APIRouter(prefix="/api", tags=["stocks"])


_STOCKS_SQL = text(
    """
    SELECT s.symbol AS code, t.company_name AS name, t.market,
           s.last_price AS price, s.change_rate
    FROM serving.symbol_snapshot s
    JOIN reference.bronze_market_ticker t ON t.ticker = s.symbol
    ORDER BY s.cumulative_volume DESC NULLS LAST
    """
)


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _to_float(value: Any) -> float | None:
    return float(value) if value is not None else None


@router.get("/stocks")
async def get_stocks(request: Request) -> list[dict[str, Any]]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_STOCKS_SQL)
        rows = result.all()
    return [
        {
            "code": row.code,
            "name": row.name,
            "market": row.market,
            "price": row.price,
            "change_rate": _to_float(row.change_rate),
        }
        for row in rows
    ]

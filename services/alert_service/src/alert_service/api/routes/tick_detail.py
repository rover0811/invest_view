"""Public read-only latest tick detail API over bronze (raw ``text()`` SQL).

Returns the most recently persisted ``bronze.tick_history`` row for a symbol
(latest-1-row projection, ordered by ``persisted_at DESC``). Numeric(18,8)/
Numeric(20,8) columns are coerced to ``float`` so they serialize as JSON numbers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


router = APIRouter(prefix="/api", tags=["tick_detail"])


_TICK_DETAIL_SQL = text(
    """
    SELECT trade_strength, buy_ratio, net_buy_count, buy_count, sell_count,
           total_buy_volume, total_sell_volume, ask_remain_1, bid_remain_1,
           total_ask_remain, total_bid_remain, volume_turnover, vwap, prev_day_volume_rate
    FROM bronze.tick_history
    WHERE symbol = :symbol
    ORDER BY persisted_at DESC
    LIMIT 1
    """
)


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _to_float(value: Any) -> float | None:
    return float(value) if value is not None else None


@router.get("/tick-detail/{symbol}")
async def get_tick_detail(request: Request, symbol: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_TICK_DETAIL_SQL, {"symbol": symbol})
        row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tick detail not found")
    return {
        "trade_strength": _to_float(row.trade_strength),
        "buy_ratio": _to_float(row.buy_ratio),
        "net_buy_count": row.net_buy_count,
        "buy_count": row.buy_count,
        "sell_count": row.sell_count,
        "total_buy_volume": row.total_buy_volume,
        "total_sell_volume": row.total_sell_volume,
        "ask_remain_1": row.ask_remain_1,
        "bid_remain_1": row.bid_remain_1,
        "total_ask_remain": row.total_ask_remain,
        "total_bid_remain": row.total_bid_remain,
        "volume_turnover": _to_float(row.volume_turnover),
        "vwap": _to_float(row.vwap),
        "prev_day_volume_rate": _to_float(row.prev_day_volume_rate),
    }

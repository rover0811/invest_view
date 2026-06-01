"""Public read-only chart data API over silver/serving/gold (raw ``text()`` SQL).

``time`` is an integer UTC epoch in SECONDS (Lightweight Charts ``UTCTimestamp``).
``extract(epoch from <timestamptz>)`` yields UTC seconds regardless of the stored
timezone, so KST-aware ``bucket_start`` / ``triggered_at`` convert correctly.
"""
from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


router = APIRouter(prefix="/api", tags=["candles"])


_CANDLES_SQL = text(
    """
    SELECT extract(epoch from bucket_start)::bigint AS time_s,
           open, high, low, close
    FROM silver.symbol_5m_metrics
    WHERE symbol = :symbol
    ORDER BY bucket_start ASC
    LIMIT :limit
    """
)

_SNAPSHOT_SQL = text(
    """
    SELECT symbol, last_price, change, change_rate, change_sign,
           cumulative_volume, vi_trigger_price, trading_halted, updated_at
    FROM serving.symbol_snapshot
    WHERE symbol = :symbol
    """
)

_TIMELINE_SQL = text(
    """
    SELECT extract(epoch from triggered_at)::bigint AS time_s,
           event_kind, event_type, triggered_at, trigger_values
    FROM serving.symbol_signal_timeline
    WHERE symbol = :symbol
    ORDER BY triggered_at ASC
    LIMIT :limit
    """
)


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _to_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _coerce_json(value: Any) -> Any:
    # asyncpg leaves JSONB undecoded (str) for raw text() selects of unknown-typed
    # columns; typed/dict results arrive as a mapping. Handle both.
    if value is None:
        return {}
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


@router.get("/candles/{symbol}")
async def get_candles(
    request: Request,
    symbol: str,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[dict[str, Any]]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_CANDLES_SQL, {"symbol": symbol, "limit": limit})
        rows = result.all()
    return [
        {
            "time": row.time_s,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
        }
        for row in rows
    ]


@router.get("/snapshot/{symbol}")
async def get_snapshot(request: Request, symbol: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_SNAPSHOT_SQL, {"symbol": symbol})
        row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="snapshot not found")
    return {
        "symbol": row.symbol,
        "last_price": row.last_price,
        "change": row.change,
        "change_rate": _to_float(row.change_rate),
        "change_sign": row.change_sign,
        "cumulative_volume": row.cumulative_volume,
        "vi_trigger_price": row.vi_trigger_price,
        "trading_halted": row.trading_halted,
        "updated_at": row.updated_at.isoformat() if row.updated_at is not None else None,
    }


@router.get("/timeline/{symbol}")
async def get_timeline(
    request: Request,
    symbol: str,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[dict[str, Any]]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_TIMELINE_SQL, {"symbol": symbol, "limit": limit})
        rows = result.all()
    return [
        {
            "time": row.time_s,
            "event_kind": row.event_kind,
            "event_type": row.event_type,
            "triggered_at": row.triggered_at.isoformat() if row.triggered_at is not None else None,
            "trigger_values": _coerce_json(row.trigger_values),
        }
        for row in rows
    ]

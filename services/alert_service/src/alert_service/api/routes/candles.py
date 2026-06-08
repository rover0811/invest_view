"""Public read-only chart data API over silver/serving/gold (raw ``text()`` SQL).

``time`` is an integer UTC epoch in SECONDS (Lightweight Charts ``UTCTimestamp``).
``extract(epoch from <timestamptz>)`` yields UTC seconds regardless of the stored
timezone, so KST-aware ``bucket_start`` / ``triggered_at`` convert correctly.

``/candles`` serves 5-minute bars (``silver.symbol_5m_metrics``) by default and
daily/weekly/monthly bars (``silver.symbol_daily_ohlc``) via the ``interval`` query
param (``5m`` | ``1d`` | ``1w`` | ``1M``). Daily ``trade_date`` is a plain ``DATE`` (no
tz); ``(trade_date::timestamp AT TIME ZONE 'Asia/Seoul')`` anchors it to KST midnight
before epoch extraction, so every interval returns the same numeric epoch-seconds
``time`` format the frontend already consumes for 5m bars.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse

from alert_service.api.price_resolution import resolve_price


router = APIRouter(prefix="/api", tags=["candles"])


class ResolvedPriceResponse(BaseModel):
    symbol: str | None
    price: int | None
    source: str
    as_of: datetime | None
    is_realtime: bool
    is_stale: bool
    display_label: str
    change: int | None
    change_rate: float | None
    change_sign: str | None
    cumulative_volume: int | None
    vi_trigger_price: int | None
    trading_halted: str | None


_CANDLES_SQL = text(
    """
    SELECT time_s, open, high, low, close
    FROM (
        SELECT extract(epoch from bucket_start)::bigint AS time_s,
               open, high, low, close
        FROM silver.symbol_5m_metrics
        WHERE symbol = :symbol
        ORDER BY bucket_start DESC
        LIMIT :limit
    ) sub
    ORDER BY time_s ASC
    """
)

_DAILY_CANDLES_SQL = text(
    """
    SELECT time_s, open, high, low, close, volume
    FROM (
        SELECT extract(epoch from (trade_date::timestamp AT TIME ZONE 'Asia/Seoul'))::bigint AS time_s,
               open, high, low, close, volume
        FROM silver.symbol_daily_ohlc
        WHERE symbol = :symbol AND "interval" = :iv
        ORDER BY trade_date DESC
        LIMIT :limit
    ) sub
    ORDER BY time_s ASC
    """
)

_DAILY_INTERVAL_CODES = {"1d": "d", "1w": "w", "1M": "m"}
_ALLOWED_INTERVALS = {"5m", *_DAILY_INTERVAL_CODES}

_SNAPSHOT_SQL = text(
    """
    SELECT symbol, last_price, change, change_rate, change_sign,
           cumulative_volume, vi_trigger_price, trading_halted, updated_at
    FROM serving.symbol_snapshot
    WHERE symbol = :symbol
    """
)

_RESOLVED_PRICE_SQL = text(
    """
    WITH snapshot AS (SELECT symbol,last_price,change,change_rate,change_sign,cumulative_volume,vi_trigger_price,trading_halted,updated_at FROM serving.symbol_snapshot WHERE symbol=CAST(:symbol AS text) LIMIT 1),
    daily AS (SELECT symbol,trade_date,close,volume,fetched_at FROM silver.symbol_daily_ohlc WHERE symbol=CAST(:symbol AS text) AND interval='d' AND close IS NOT NULL ORDER BY trade_date DESC LIMIT 1)
    SELECT CAST(:symbol AS text) AS requested_symbol,
     s.symbol AS snapshot_symbol, s.last_price AS snapshot_last_price, s.change AS snapshot_change, s.change_rate AS snapshot_change_rate, s.change_sign AS snapshot_change_sign, s.cumulative_volume AS snapshot_cumulative_volume, s.vi_trigger_price AS snapshot_vi_trigger_price, s.trading_halted AS snapshot_trading_halted, s.updated_at AS snapshot_updated_at,
     d.symbol AS daily_symbol, d.trade_date AS daily_trade_date, d.close AS daily_close, d.volume AS daily_volume, d.fetched_at AS daily_fetched_at
    FROM (SELECT 1) one LEFT JOIN snapshot s ON true LEFT JOIN daily d ON true;
    """
)

_TIMELINE_SQL = text(
    """
    SELECT time_s, event_kind, event_type, triggered_at, trigger_values
    FROM (
        SELECT extract(epoch from triggered_at)::bigint AS time_s,
               event_kind, event_type, triggered_at, trigger_values
        FROM serving.symbol_signal_timeline
        WHERE symbol = :symbol
        ORDER BY triggered_at DESC
        LIMIT :limit
    ) sub
    ORDER BY time_s ASC
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


async def _load_resolved_price(request: Request, symbol: str) -> dict[str, Any]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_RESOLVED_PRICE_SQL, {"symbol": symbol})
        row = result.mappings().one()

    snapshot = None
    if row["snapshot_symbol"] is not None:
        snapshot = {
            "symbol": row["snapshot_symbol"],
            "last_price": row["snapshot_last_price"],
            "change": row["snapshot_change"],
            "change_rate": _to_float(row["snapshot_change_rate"]),
            "change_sign": row["snapshot_change_sign"],
            "cumulative_volume": row["snapshot_cumulative_volume"],
            "vi_trigger_price": row["snapshot_vi_trigger_price"],
            "trading_halted": row["snapshot_trading_halted"],
            "updated_at": row["snapshot_updated_at"],
        }

    daily = None
    if row["daily_symbol"] is not None:
        daily = {
            "symbol": row["daily_symbol"],
            "trade_date": row["daily_trade_date"],
            "close": row["daily_close"],
            "volume": row["daily_volume"],
            "fetched_at": row["daily_fetched_at"],
        }

    resolved = resolve_price(
        snapshot,
        daily,
        now_utc=datetime.now(timezone.utc),
        ttl_seconds=request.app.state.config.price_realtime_ttl_seconds,
    )
    if resolved["symbol"] is None:
        resolved["symbol"] = row["requested_symbol"]
    resolved["as_of"] = resolved["as_of"].isoformat() if resolved["as_of"] is not None else None
    return resolved


def _price_stream_key(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        payload.get("price"),
        payload.get("source"),
        payload.get("as_of"),
        payload.get("is_realtime"),
        payload.get("is_stale"),
        payload.get("change"),
        payload.get("change_rate"),
        payload.get("change_sign"),
        payload.get("cumulative_volume"),
        payload.get("vi_trigger_price"),
        payload.get("trading_halted"),
    )


async def _price_stream_events(
    request: Request,
    symbol: str,
    *,
    poll_seconds: float = 0.5,
    keepalive_seconds: float = 20.0,
) -> AsyncIterator[str]:
    last_key: tuple[Any, ...] | None = None
    last_keepalive = time.monotonic()
    try:
        while True:
            if await request.is_disconnected():
                return

            payload = await _load_resolved_price(request, symbol)
            key = _price_stream_key(payload)
            now = time.monotonic()

            if last_key is None or key != last_key:
                last_key = key
                last_keepalive = now
                yield f"event: price\ndata: {json.dumps(payload)}\n\n"
            elif now - last_keepalive >= keepalive_seconds:
                last_keepalive = now
                yield ": keepalive\n\n"

            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"


@router.get("/candles/{symbol}")
async def get_candles(
    request: Request,
    symbol: str,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    interval: Annotated[str, Query()] = "5m",
) -> list[dict[str, Any]]:
    if interval not in _ALLOWED_INTERVALS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unsupported interval: {interval!r}",
        )
    session_factory = _session_factory(request)
    if interval == "5m":
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
    async with session_factory() as session:
        result = await session.execute(
            _DAILY_CANDLES_SQL,
            {"symbol": symbol, "iv": _DAILY_INTERVAL_CODES[interval], "limit": limit},
        )
        rows = result.all()
    return [
        {
            "time": row.time_s,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
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


@router.get("/price/{symbol}", response_model=ResolvedPriceResponse)
async def get_price(request: Request, symbol: str) -> dict[str, Any]:
    return await _load_resolved_price(request, symbol)


@router.get("/price-stream/{symbol}")
async def price_stream(request: Request, symbol: str) -> StreamingResponse:
    return StreamingResponse(
        _price_stream_events(request, symbol),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

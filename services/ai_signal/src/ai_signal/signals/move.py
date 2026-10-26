"""The move a signal request applies to and its candidate range (12.1, 18.10 "move")."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.market_data.repository import KST

DAILY = "DAILY"
LAST_SESSION_END = time(20, 0)
DIRECTION_BY_PRICE_STATE: Mapping[str, int] = {
    "POSITIVE": 1,
    "NEGATIVE": -1,
    "NEUTRAL": 0,
}


@dataclass(frozen=True)
class Contributions:
    global_: Decimal
    market: Decimal
    sector: Decimal
    idio: Decimal
    dominant_layer: str | None


@dataclass(frozen=True)
class OpenMove:
    move_id: UUID
    move_type: str
    symbol: str
    symbol_name: str
    symbol_entity_id: UUID
    signal_enabled: bool
    window_start: datetime
    window_end: datetime
    return_pct: Decimal
    price_state: str
    contributions: Contributions | None
    version: int

    @property
    def direction(self) -> int:
        return DIRECTION_BY_PRICE_STATE[self.price_state]


@dataclass(frozen=True)
class CandidateRange:
    start: datetime
    end: datetime


LATEST_MOVE = text(
    """
    SELECT m.move_id, m.move_type, es.ticker, e.name, m.symbol_entity_id,
           es.signal_enabled, m.window_start, m.window_end, m.return_pct,
           m.price_state, m.contrib_global, m.contrib_market, m.contrib_sector,
           m.contrib_idio, m.dominant_layer, m.version
    FROM gold.move m
    JOIN reference.entity_symbol es ON es.entity_id = m.symbol_entity_id
    JOIN reference.entity e ON e.entity_id = m.symbol_entity_id
    WHERE es.ticker = :symbol
    ORDER BY m.window_end DESC, m.version DESC
    LIMIT 1
    """
)


async def latest_move(session: AsyncSession, symbol: str) -> OpenMove | None:
    """The latest move of the symbol by ``window_end``; requests carry no move id (16.1)."""
    row = (await session.execute(LATEST_MOVE, {"symbol": symbol})).first()
    if row is None:
        return None
    layers = (row[10], row[11], row[12], row[13])
    contributions = (
        Contributions(*(Decimal(value) for value in layers), dominant_layer=row[14])
        if all(value is not None for value in layers)
        else None
    )
    return OpenMove(
        move_id=row[0],
        move_type=str(row[1]),
        symbol=str(row[2]),
        symbol_name=str(row[3]),
        symbol_entity_id=row[4],
        signal_enabled=bool(row[5]),
        window_start=row[6],
        window_end=row[7],
        return_pct=Decimal(row[8]),
        price_state=str(row[9]),
        contributions=contributions,
        version=int(row[15]),
    )


async def locked_move_version(session: AsyncSession, move_id: UUID) -> int | None:
    result = await session.execute(
        text("SELECT version FROM gold.move WHERE move_id = :id FOR UPDATE"),
        {"id": move_id},
    )
    row = result.first()
    return int(row[0]) if row is not None else None


def daily_range(move: OpenMove) -> CandidateRange:
    business_date = move.window_end.astimezone(KST).date()
    session_end = datetime.combine(business_date, LAST_SESSION_END, tzinfo=KST)
    return CandidateRange(
        start=move.window_start, end=max(session_end, move.window_end)
    )


CANDIDATE_RANGES: Mapping[str, Callable[[OpenMove], CandidateRange]] = {
    DAILY: daily_range,
}


def candidate_range(move: OpenMove) -> CandidateRange:
    """Previous KRX close (``window_start``, set by the move sink) to 20:00 KST."""
    rule = CANDIDATE_RANGES.get(move.move_type)
    if rule is None:
        raise ValueError(f"no candidate range rule for move type {move.move_type}")
    return rule(move)

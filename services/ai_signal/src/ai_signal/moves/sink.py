"""Move sink: ``price-moves`` to ``gold.move`` (doc 22 section 11, 18.10).

Idempotency follows doc 20: ``gold.move`` is keyed by ``move_id`` and only a
strictly newer ``version`` replaces a row. A redelivered version only fills
contributions that were still null. The candidate range start is recomputed
as the previous trading day's 15:30 KST close so holidays are skipped.
A new version is a price state transition and requests a signal for symbols
with ``signal_enabled``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from ai_signal.consumers.frame import ConsumedMessage, HandlerContext
from ai_signal.documents.linking import ENTITY_SYMBOL
from ai_signal.market_data.repository import KST
from ai_signal.market_data.trading_calendar import previous_close_at
from ai_signal.moves.decompose import Decomposition, decompose_daily
from ai_signal.moves.pending import contrib_params
from ai_signal.signal_requests import (
    RequestTrigger,
    SignalRequestReason,
    publish_signal_requests,
    request_targets,
)

logger = logging.getLogger(__name__)

PRICE_MOVES_TOPIC = "price-moves"
DAILY = "DAILY"


@dataclass(frozen=True)
class MoveRecord:
    move_id: UUID
    move_type: str
    symbol: str
    window_start: datetime
    window_end: datetime
    return_pct: Decimal
    price_state: str
    signal_status: str
    version: int

    @property
    def business_date(self) -> date:
        return self.window_end.astimezone(KST).date()

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> MoveRecord:
        return cls(
            move_id=UUID(str(value["move_id"])),
            move_type=str(value["move_type"]),
            symbol=str(value["symbol"]),
            window_start=value["window_start"],
            window_end=value["window_end"],
            return_pct=Decimal(str(value["return_pct"])),
            price_state=str(value["price_state"]),
            signal_status=str(value["signal_status"]),
            version=int(value["version"]),
        )


@dataclass(frozen=True)
class SymbolGate:
    entity_id: UUID
    signal_enabled: bool


@dataclass(frozen=True)
class StoredMove:
    version: int
    decomposed: bool


async def symbol_gate(session: AsyncSession, ticker: str) -> SymbolGate | None:
    result = await session.execute(
        sa.select(ENTITY_SYMBOL.c.entity_id, ENTITY_SYMBOL.c.signal_enabled).where(
            ENTITY_SYMBOL.c.ticker == ticker
        )
    )
    row = result.first()
    return SymbolGate(row[0], bool(row[1])) if row is not None else None


async def stored_move(session: AsyncSession, move_id: UUID) -> StoredMove | None:
    result = await session.execute(
        text(
            "SELECT version, contrib_idio IS NOT NULL FROM gold.move "
            "WHERE move_id = :id FOR UPDATE"
        ),
        {"id": move_id},
    )
    row = result.first()
    return StoredMove(int(row[0]), bool(row[1])) if row is not None else None


UPSERT_MOVE = text(
    """
    INSERT INTO gold.move (
        move_id, move_type, symbol_entity_id, window_start, window_end,
        return_pct, price_state, contrib_global, contrib_market, contrib_sector,
        contrib_idio, beta_method, beta_as_of, dominant_layer, signal_status, version
    ) VALUES (
        :move_id, :move_type, :symbol_entity_id, :window_start, :window_end,
        :return_pct, :price_state, :contrib_global, :contrib_market, :contrib_sector,
        :contrib_idio, :beta_method, :beta_as_of, :dominant_layer, :signal_status,
        :version
    )
    ON CONFLICT (move_id) DO UPDATE SET
        window_start = EXCLUDED.window_start,
        window_end = EXCLUDED.window_end,
        return_pct = EXCLUDED.return_pct,
        price_state = EXCLUDED.price_state,
        contrib_global = EXCLUDED.contrib_global,
        contrib_market = EXCLUDED.contrib_market,
        contrib_sector = EXCLUDED.contrib_sector,
        contrib_idio = EXCLUDED.contrib_idio,
        beta_method = EXCLUDED.beta_method,
        beta_as_of = EXCLUDED.beta_as_of,
        dominant_layer = EXCLUDED.dominant_layer,
        version = EXCLUDED.version
    WHERE gold.move.version < EXCLUDED.version
       OR (gold.move.version = EXCLUDED.version AND gold.move.contrib_idio IS NULL)
    """
)


async def upsert_move(
    session: AsyncSession,
    record: MoveRecord,
    symbol_id: UUID,
    window_start: datetime,
    decomposition: Decomposition | None,
) -> None:
    await session.execute(
        UPSERT_MOVE,
        {
            "move_id": record.move_id,
            "move_type": record.move_type,
            "symbol_entity_id": symbol_id,
            "window_start": window_start,
            "window_end": record.window_end,
            "return_pct": record.return_pct,
            "price_state": record.price_state,
            "signal_status": record.signal_status,
            "version": record.version,
            **contrib_params(decomposition),
        },
    )


async def candidate_start(session: AsyncSession, record: MoveRecord) -> datetime:
    start = await previous_close_at(session, record.business_date)
    if start is None:
        logger.warning(
            "no trading day before %s; keeping window_start of move %s",
            record.business_date,
            record.move_id,
        )
        return record.window_start
    if start != record.window_start:
        logger.warning(
            "move %s window_start %s differs from previous close %s; using the latter",
            record.move_id,
            record.window_start.isoformat(),
            start.isoformat(),
        )
    return start


async def _decompose(
    conn: AsyncConnection, move_type: str, ticker: str, day: date
) -> Decomposition | None:
    if move_type != DAILY:
        return None
    return await decompose_daily(conn, ticker, day)


class MoveSink:
    async def __call__(self, message: ConsumedMessage, context: HandlerContext) -> None:
        session = context.session
        record = MoveRecord.from_value(message.value)
        gate = await symbol_gate(session, record.symbol)
        if gate is None:
            logger.warning(
                "move %s for unknown symbol %s", record.move_id, record.symbol
            )
            return
        stored = await stored_move(session, record.move_id)
        if stored is not None and (
            stored.version > record.version
            or (stored.version == record.version and stored.decomposed)
        ):
            logger.info(
                "skip move %s v%d (stored v%d)",
                record.move_id,
                record.version,
                stored.version,
            )
            return
        window_start = await candidate_start(session, record)
        decomposition = await _decompose(
            await session.connection(),
            record.move_type,
            record.symbol,
            record.business_date,
        )
        await upsert_move(session, record, gate.entity_id, window_start, decomposition)
        is_transition = stored is None or stored.version < record.version
        if not (is_transition and gate.signal_enabled):
            return
        targets = await request_targets(
            session, RequestTrigger("SYMBOL", (record.symbol,))
        )
        await publish_signal_requests(
            context.outbox,
            targets,
            SignalRequestReason.PRICE_STATE_CHANGE,
            record.window_end,
        )

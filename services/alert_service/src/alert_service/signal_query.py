"""AI signal queries over the ``serving`` schema only (doc 22 15.3, 18.7).

Plain async functions with typed arguments so the REST routes and the later
``/mcp`` tools call the same code. The history date is the KST trading date of
``move_window_end`` (event time).

``headline`` is assembled from the live ``serving.symbol_snapshot.change_rate``
(see ``alert_service.headline``), so it is set only on the current signal when
it is SHOWN and a snapshot exists. History versions keep ``headline_label``
and return ``headline`` as null, because today's price does not describe a
past version.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.api.schemas.signal import SignalCauseOut, SignalOut
from alert_service.headline import assemble_headline


_SIGNAL_COLUMNS = (
    "symbol, symbol_name, move_id, move_version, version, move_window_end, status, "
    "headline_label, generation_method, updated_at"
)

_CURRENT_SQL = text(
    f"SELECT {_SIGNAL_COLUMNS} FROM serving.symbol_signal WHERE symbol = :symbol"
)
_CHANGE_RATE_SQL = text(
    "SELECT change_rate FROM serving.symbol_snapshot WHERE symbol = :symbol"
)
_CURRENT_CAUSES_SQL = text(
    """
    SELECT c.rank, c.explanation, c.source_count,
           COALESCE(
               array_agg(s.document_id ORDER BY s.document_id)
                   FILTER (WHERE s.document_id IS NOT NULL),
               '{}'
           ) AS source_document_ids
    FROM serving.symbol_signal_cause c
    LEFT JOIN serving.symbol_signal_cause_source s
        ON s.symbol = c.symbol AND s.rank = c.rank
    WHERE c.symbol = :symbol
    GROUP BY c.rank, c.explanation, c.source_count
    ORDER BY c.rank
    """
)
_HISTORY_SQL = text(
    f"""
    SELECT {_SIGNAL_COLUMNS}
    FROM serving.symbol_signal_history
    WHERE symbol = :symbol
      AND (move_window_end AT TIME ZONE 'Asia/Seoul')::date = :day
    ORDER BY move_window_end, move_id, move_version, version
    """
)
_HISTORY_CAUSES_SQL = text(
    """
    SELECT c.move_id, c.version, c.rank, c.explanation, c.source_count,
           COALESCE(
               array_agg(s.document_id ORDER BY s.document_id)
                   FILTER (WHERE s.document_id IS NOT NULL),
               '{}'
           ) AS source_document_ids
    FROM serving.symbol_signal_history_cause c
    LEFT JOIN serving.symbol_signal_history_cause_source s
        ON s.move_id = c.move_id AND s.version = c.version AND s.rank = c.rank
    WHERE c.move_id IN :move_ids
    GROUP BY c.move_id, c.version, c.rank, c.explanation, c.source_count
    ORDER BY c.move_id, c.version, c.rank
    """
).bindparams(bindparam("move_ids", expanding=True))


def _cause(row: Any) -> SignalCauseOut:
    return SignalCauseOut(
        rank=row.rank,
        explanation=row.explanation,
        source_count=row.source_count,
        source_document_ids=list(row.source_document_ids),
    )


def _headline(row: Any, change_rate: Decimal | None) -> str | None:
    if row.status != "SHOWN" or not row.headline_label or change_rate is None:
        return None
    return assemble_headline(row.headline_label, row.symbol_name, change_rate)


def _signal(
    row: Any, causes: list[SignalCauseOut], headline: str | None = None
) -> SignalOut:
    return SignalOut(
        symbol=row.symbol,
        symbol_name=row.symbol_name,
        move_id=row.move_id,
        move_version=row.move_version,
        version=row.version,
        move_window_end=row.move_window_end,
        status=row.status,
        headline_label=row.headline_label,
        headline=headline,
        generation_method=row.generation_method,
        updated_at=row.updated_at,
        causes=causes,
    )


async def get_current_signal(
    session_factory: async_sessionmaker[AsyncSession], symbol: str
) -> SignalOut | None:
    async with session_factory() as session:
        row = (await session.execute(_CURRENT_SQL, {"symbol": symbol})).first()
        if row is None:
            return None
        causes = (
            await session.execute(_CURRENT_CAUSES_SQL, {"symbol": symbol})
        ).all()
        change_rate = (
            await session.execute(_CHANGE_RATE_SQL, {"symbol": symbol})
        ).scalar_one_or_none()
    return _signal(
        row, [_cause(cause) for cause in causes], _headline(row, change_rate)
    )


async def get_signal_history(
    session_factory: async_sessionmaker[AsyncSession], symbol: str, day: date
) -> list[SignalOut]:
    async with session_factory() as session:
        rows = (
            await session.execute(_HISTORY_SQL, {"symbol": symbol, "day": day})
        ).all()
        if not rows:
            return []
        move_ids = sorted({row.move_id for row in rows})
        cause_rows = (
            await session.execute(_HISTORY_CAUSES_SQL, {"move_ids": move_ids})
        ).all()
    causes: dict[tuple[UUID, int], list[SignalCauseOut]] = defaultdict(list)
    for cause in cause_rows:
        causes[(cause.move_id, cause.version)].append(_cause(cause))
    return [_signal(row, causes[(row.move_id, row.version)]) for row in rows]

"""Serving sink: ``signal-updates`` to the ``serving`` schema (doc 22 15.2, 18.7).

Ordering uses event-time keys only, never ``updated_at``:

- same move as the stored row: apply only if ``(move_version, version)`` is
  strictly greater
- different move: apply only if ``(move_window_end, move_id)`` is strictly
  greater, so a newer move replaces the previous move's row and a late update
  of an older move is ignored

The upsert carries the ordering condition in its ``WHERE`` clause, so the
decision and the write are one statement. Causes are replaced in the same
transaction and kept only for SHOWN (15.2), together with each cause's
DOCUMENT ids. Every version is also appended to
the history tables keyed by ``(move_id, version)``, which makes redelivery a
no-op.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.consumers.frame import ConsumedMessage, HandlerContext
from ai_signal.signals.store import SIGNAL_UPDATES_TOPIC

logger = logging.getLogger(__name__)

SHOWN = "SHOWN"
KNOWN_STATUSES = frozenset({SHOWN, "HIDDEN", "NO_CANDIDATE"})
MAX_CAUSE_RANK = 3

__all__ = ["SIGNAL_UPDATES_TOPIC", "ServingSink", "SignalUpdate", "apply_update"]


@dataclass(frozen=True)
class UpdateCause:
    rank: int
    explanation: str
    source_count: int
    source_document_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class SignalUpdate:
    symbol: str
    symbol_name: str
    move_id: UUID
    version: int
    move_version: int
    move_window_end: datetime
    status: str
    headline_label: str | None
    generation_method: str | None
    causes: tuple[UpdateCause, ...]
    updated_at: datetime

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> SignalUpdate:
        method = value.get("generation_method")
        return cls(
            symbol=str(value["symbol"]),
            symbol_name=str(value["symbol_name"]),
            move_id=UUID(str(value["move_id"])),
            version=int(value["version"]),
            move_version=int(value["move_version"]),
            move_window_end=value["move_window_end"],
            status=str(value["status"]),
            headline_label=value.get("headline_label"),
            generation_method=None if method is None else str(method),
            causes=tuple(
                UpdateCause(
                    rank=int(cause["rank"]),
                    explanation=str(cause["explanation"]),
                    source_count=int(cause["source_count"]),
                    source_document_ids=tuple(
                        UUID(str(document_id))
                        for document_id in cause.get("source_document_ids") or ()
                    ),
                )
                for cause in value.get("causes") or ()
            ),
            updated_at=value["updated_at"],
        )

    def served_causes(self) -> tuple[UpdateCause, ...]:
        if self.status != SHOWN:
            return ()
        valid = tuple(
            cause
            for cause in self.causes
            if 1 <= cause.rank <= MAX_CAUSE_RANK
            and cause.source_count >= 1
            and cause.source_count == len(cause.source_document_ids)
            and len(set(cause.source_document_ids)) == cause.source_count
        )
        if len(valid) != len(self.causes):
            logger.warning(
                "serving sink: dropped %d invalid causes move_id=%s version=%s",
                len(self.causes) - len(valid),
                self.move_id,
                self.version,
            )
        return valid


_UPSERT_CURRENT = text(
    """
    INSERT INTO serving.symbol_signal AS cur (
        symbol, symbol_name, move_id, version, move_version, move_window_end,
        status, headline_label, generation_method, updated_at
    ) VALUES (
        :symbol, :symbol_name, :move_id, :version, :move_version,
        :move_window_end, :status, :headline_label, :generation_method,
        :updated_at
    )
    ON CONFLICT (symbol) DO UPDATE SET
        symbol_name = EXCLUDED.symbol_name,
        move_id = EXCLUDED.move_id,
        version = EXCLUDED.version,
        move_version = EXCLUDED.move_version,
        move_window_end = EXCLUDED.move_window_end,
        status = EXCLUDED.status,
        headline_label = EXCLUDED.headline_label,
        generation_method = EXCLUDED.generation_method,
        updated_at = EXCLUDED.updated_at
    WHERE (
        cur.move_id = EXCLUDED.move_id
        AND (EXCLUDED.move_version, EXCLUDED.version)
            > (cur.move_version, cur.version)
    ) OR (
        cur.move_id <> EXCLUDED.move_id
        AND (EXCLUDED.move_window_end, EXCLUDED.move_id::text)
            > (cur.move_window_end, cur.move_id::text)
    )
    RETURNING symbol
    """
)
_DELETE_CURRENT_CAUSES = text(
    "DELETE FROM serving.symbol_signal_cause WHERE symbol = :symbol"
)
_INSERT_CURRENT_CAUSE = text(
    """
    INSERT INTO serving.symbol_signal_cause (symbol, rank, explanation, source_count)
    VALUES (:symbol, :rank, :explanation, :source_count)
    """
)
_INSERT_CURRENT_CAUSE_SOURCE = text(
    """
    INSERT INTO serving.symbol_signal_cause_source (symbol, rank, document_id)
    VALUES (:symbol, :rank, :document_id)
    """
)
_INSERT_HISTORY = text(
    """
    INSERT INTO serving.symbol_signal_history (
        move_id, version, symbol, symbol_name, move_version, move_window_end,
        status, headline_label, generation_method, updated_at
    ) VALUES (
        :move_id, :version, :symbol, :symbol_name, :move_version,
        :move_window_end, :status, :headline_label, :generation_method,
        :updated_at
    )
    ON CONFLICT (move_id, version) DO NOTHING
    RETURNING move_id
    """
)
_INSERT_HISTORY_CAUSE = text(
    """
    INSERT INTO serving.symbol_signal_history_cause (
        move_id, version, rank, explanation, source_count
    ) VALUES (:move_id, :version, :rank, :explanation, :source_count)
    """
)
_INSERT_HISTORY_CAUSE_SOURCE = text(
    """
    INSERT INTO serving.symbol_signal_history_cause_source (
        move_id, version, rank, document_id
    ) VALUES (:move_id, :version, :rank, :document_id)
    """
)


def _row_params(update: SignalUpdate) -> dict[str, Any]:
    return {
        "symbol": update.symbol,
        "symbol_name": update.symbol_name,
        "move_id": update.move_id,
        "version": update.version,
        "move_version": update.move_version,
        "move_window_end": update.move_window_end,
        "status": update.status,
        "headline_label": update.headline_label,
        "generation_method": update.generation_method,
        "updated_at": update.updated_at,
    }


def _cause_params(causes: Sequence[UpdateCause], **keys: Any) -> list[dict[str, Any]]:
    return [
        {
            **keys,
            "rank": cause.rank,
            "explanation": cause.explanation,
            "source_count": cause.source_count,
        }
        for cause in causes
    ]


def _source_params(causes: Sequence[UpdateCause], **keys: Any) -> list[dict[str, Any]]:
    return [
        {**keys, "rank": cause.rank, "document_id": document_id}
        for cause in causes
        for document_id in cause.source_document_ids
    ]


async def apply_update(session: AsyncSession, update: SignalUpdate) -> bool:
    """Write one update; return True if it became the symbol's current row."""
    causes = update.served_causes()
    row = _row_params(update)

    history = await session.execute(_INSERT_HISTORY, row)
    if history.first() is not None and causes:
        await session.execute(
            _INSERT_HISTORY_CAUSE,
            _cause_params(causes, move_id=update.move_id, version=update.version),
        )
        await session.execute(
            _INSERT_HISTORY_CAUSE_SOURCE,
            _source_params(causes, move_id=update.move_id, version=update.version),
        )

    current = await session.execute(_UPSERT_CURRENT, row)
    if current.first() is None:
        return False
    await session.execute(_DELETE_CURRENT_CAUSES, {"symbol": update.symbol})
    if causes:
        await session.execute(
            _INSERT_CURRENT_CAUSE, _cause_params(causes, symbol=update.symbol)
        )
        await session.execute(
            _INSERT_CURRENT_CAUSE_SOURCE, _source_params(causes, symbol=update.symbol)
        )
    return True


class ServingSink:
    async def __call__(self, message: ConsumedMessage, ctx: HandlerContext) -> None:
        update = SignalUpdate.from_value(message.value)
        if update.status not in KNOWN_STATUSES:
            logger.warning(
                "serving sink: skipping unknown status=%s move_id=%s version=%s",
                update.status,
                update.move_id,
                update.version,
            )
            return
        applied = await apply_update(ctx.session, update)
        logger.info(
            "serving sink: symbol=%s move_id=%s move_version=%s version=%s applied=%s",
            update.symbol,
            update.move_id,
            update.move_version,
            update.version,
            applied,
        )

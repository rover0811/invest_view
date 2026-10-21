"""gold.signal ledger, signal_retry rows and ``signal-updates`` events (12.3, 12.5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.llm import LlmErrorKind
from ai_signal.outbox.writer import OutboxWriter
from ai_signal.signals.generation import GeneratedCause, Generation
from ai_signal.signals.move import OpenMove

SIGNAL_UPDATES_TOPIC = "signal-updates"
RETRY_FIRST_DELAY = timedelta(seconds=30)
_EVENT_NAMESPACE = uuid5(NAMESPACE_URL, "invest-view/ai-signal/signal-updates")


@dataclass(frozen=True)
class StoredSignal:
    version: int
    input_hash: str | None
    status: str = "SHOWN"
    move_version: int = 0
    as_of_event_time: datetime | None = None


async def latest_signal(session: AsyncSession, move_id: UUID) -> StoredSignal | None:
    result = await session.execute(
        text(
            "SELECT version, input_hash, status, move_version, as_of_event_time "
            "FROM gold.signal WHERE move_id = :id ORDER BY version DESC LIMIT 1"
        ),
        {"id": move_id},
    )
    row = result.first()
    if row is None:
        return None
    return StoredSignal(int(row[0]), row[1], str(row[2]), int(row[3]), row[4])


async def next_signal_version(session: AsyncSession, move_id: UUID) -> int:
    """Call with the gold.move row locked so the counter cannot race."""
    result = await session.execute(
        text(
            "SELECT COALESCE(max(version), 0) + 1 FROM gold.signal WHERE move_id = :id"
        ),
        {"id": move_id},
    )
    return int(result.scalar_one())


INSERT_SIGNAL = text(
    """
    INSERT INTO gold.signal (
        move_id, version, status, move_version, headline_cause_rank, decided_by,
        model_version, prompt_version, decided_at, probability, input_hash,
        llm_attempts, as_of_event_time
    ) VALUES (
        :move_id, :version, :status, :move_version, :headline_cause_rank,
        :decided_by, :model_version, :prompt_version, :decided_at, :probability,
        :input_hash, CAST(:llm_attempts AS jsonb), :as_of_event_time
    )
    ON CONFLICT (move_id, version) DO NOTHING
    """
)
INSERT_CAUSE = text(
    """
    INSERT INTO gold.signal_cause (
        move_id, version, rank, label, explanation, scope, timing, lag_seconds
    ) VALUES (
        :move_id, :version, :rank, :label, :explanation, :scope, :timing,
        :lag_seconds
    )
    ON CONFLICT DO NOTHING
    """
)
INSERT_CAUSE_SOURCE = text(
    """
    INSERT INTO gold.signal_cause_source (move_id, version, rank, source_type, source_id)
    VALUES (:move_id, :version, :rank, :source_type, :source_id)
    ON CONFLICT DO NOTHING
    """
)


async def insert_signal(
    session: AsyncSession,
    move: OpenMove,
    version: int,
    generation: Generation,
    input_hash: str,
    decided_at: datetime,
    as_of_event_time: datetime | None = None,
) -> None:
    decision = generation.decision
    probability = (
        Decimal(str(round(decision.probability, 4)))
        if decision.probability is not None
        else None
    )
    _ = await session.execute(
        INSERT_SIGNAL,
        {
            "move_id": move.move_id,
            "version": version,
            "status": decision.status,
            "move_version": move.version,
            "headline_cause_rank": decision.headline_cause_rank,
            "decided_by": generation.decided_by,
            "model_version": generation.model_version,
            "prompt_version": generation.prompt_version,
            "decided_at": decided_at,
            "probability": probability,
            "input_hash": input_hash,
            "as_of_event_time": as_of_event_time,
            "llm_attempts": (
                json.dumps(generation.llm_attempts, ensure_ascii=False)
                if generation.attempts
                else None
            ),
        },
    )
    for cause in decision.causes:
        earliest = min(source.published_at for source in cause.sources)
        lagging = earliest > move.window_end
        _ = await session.execute(
            INSERT_CAUSE,
            {
                "move_id": move.move_id,
                "version": version,
                "rank": cause.rank,
                "label": cause.label,
                "explanation": cause.explanation,
                "scope": cause.scope,
                "timing": "LAGGING" if lagging else "LEADING",
                "lag_seconds": (
                    int((earliest - move.window_end).total_seconds()) if lagging else 0
                ),
            },
        )
        for source in cause.sources:
            _ = await session.execute(
                INSERT_CAUSE_SOURCE,
                {
                    "move_id": move.move_id,
                    "version": version,
                    "rank": cause.rank,
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                },
            )


async def set_move_status(session: AsyncSession, move: OpenMove, status: str) -> None:
    _ = await session.execute(
        text(
            "UPDATE gold.move SET signal_status = :status "
            "WHERE move_id = :id AND version = :version"
        ),
        {"status": status, "id": move.move_id, "version": move.version},
    )


async def record_retry(
    session: AsyncSession, move: OpenMove, error: LlmErrorKind, now: datetime
) -> None:
    _ = await session.execute(
        text(
            """
            INSERT INTO gold.signal_retry (
                move_id, target_version, attempts, next_attempt_at, last_error
            ) VALUES (:id, :target, 0, :next_at, :error)
            ON CONFLICT (move_id) DO UPDATE SET
                target_version = GREATEST(
                    gold.signal_retry.target_version, EXCLUDED.target_version
                ),
                next_attempt_at = LEAST(
                    gold.signal_retry.next_attempt_at, EXCLUDED.next_attempt_at
                ),
                last_error = EXCLUDED.last_error
            """
        ),
        {
            "id": move.move_id,
            "target": move.version,
            "next_at": now + RETRY_FIRST_DELAY,
            "error": error.value,
        },
    )


async def clear_retry(session: AsyncSession, move: OpenMove) -> None:
    _ = await session.execute(
        text(
            "DELETE FROM gold.signal_retry "
            "WHERE move_id = :id AND target_version <= :version"
        ),
        {"id": move.move_id, "version": move.version},
    )


def update_event_id(move_id: UUID, version: int) -> UUID:
    return uuid5(_EVENT_NAMESPACE, f"{move_id}:{version}")


def _update_cause(cause: GeneratedCause) -> dict[str, object]:
    """DOCUMENT ids exactly as written to gold.signal_cause_source for the cause."""
    document_ids = list(
        dict.fromkeys(
            str(source.source_id)
            for source in cause.sources
            if source.source_type == "DOCUMENT"
        )
    )
    return {
        "rank": cause.rank,
        "explanation": cause.explanation,
        "source_count": len(document_ids),
        "source_document_ids": document_ids,
    }


def update_value(
    move: OpenMove, version: int, generation: Generation, now: datetime
) -> dict[str, object]:
    decision = generation.decision
    return {
        "symbol": move.symbol,
        "symbol_name": move.symbol_name,
        "move_id": str(move.move_id),
        "version": version,
        "move_version": move.version,
        "move_window_end": move.window_end,
        "status": decision.status,
        "headline_label": decision.headline_label,
        "generation_method": generation.decided_by,
        "causes": [_update_cause(cause) for cause in decision.causes],
        "updated_at": now,
    }


async def publish_update(
    outbox: OutboxWriter,
    move: OpenMove,
    version: int,
    generation: Generation,
    now: datetime,
) -> UUID:
    return await outbox.write(
        topic=SIGNAL_UPDATES_TOPIC,
        key=move.symbol,
        value=update_value(move, version, generation, now),
        event_id=update_event_id(move.move_id, version),
    )

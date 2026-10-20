"""Signal candidate sources (12.1, 18.10 "시그널").

``CANDIDATE_SOURCES`` is the registered list. Every source receives the move, its
candidate range and the target symbols (phase 1: the move's own symbol; phase 2
widens it with ``SUBSIDIARY_OF`` and ``COMPETES_WITH``). Phase 1 has documents
only; ``EventCandidateSource`` returns nothing until target price and flow
events exist.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.signals.move import CandidateRange, OpenMove

SourceType = Literal["DOCUMENT", "EVENT"]
Timing = Literal["LEADING", "LAGGING"]
MAX_DOCUMENTS = 20


@dataclass(frozen=True)
class SignalCandidate:
    ref: str
    source_type: SourceType
    source_id: UUID
    source_version: int
    title: str
    publisher: str | None
    published_at: datetime
    scope: str
    timing: Timing


@dataclass(frozen=True)
class CandidateSet:
    documents: tuple[SignalCandidate, ...] = ()
    events: tuple[SignalCandidate, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.documents and not self.events

    def by_ref(self) -> dict[str, SignalCandidate]:
        return {c.ref: c for c in (*self.documents, *self.events)}


class CandidateSource(Protocol):
    @property
    def source_type(self) -> SourceType: ...

    async def candidates(
        self,
        session: AsyncSession,
        move: OpenMove,
        window: CandidateRange,
        targets: Sequence[str],
    ) -> Sequence[SignalCandidate]: ...


def timing_of(published_at: datetime, move: OpenMove) -> Timing:
    return "LAGGING" if published_at > move.window_end else "LEADING"


DOCUMENT_CANDIDATES = text(
    """
    SELECT DISTINCT d.document_id, d.version, d.title, d.publisher,
           d.published_at, j.scope
    FROM silver.document d
    JOIN silver.document_judgement j
      ON j.document_id = d.document_id AND j.version = d.version
    JOIN silver.document_entity de ON de.document_id = d.document_id
    JOIN reference.entity_symbol es ON es.entity_id = de.entity_id
    WHERE es.ticker IN :targets
      AND j.fail_reason IS NULL
      AND j.direction = :direction
      AND d.published_at >= :start
      AND d.published_at <= :end
    ORDER BY d.published_at DESC, d.document_id
    LIMIT :limit
    """
).bindparams(bindparam("targets", expanding=True))


@dataclass(frozen=True)
class DocumentCandidateSource:
    """Judged documents without ``fail_reason`` in the move's direction pool."""

    limit: int = MAX_DOCUMENTS
    source_type: SourceType = "DOCUMENT"

    async def candidates(
        self,
        session: AsyncSession,
        move: OpenMove,
        window: CandidateRange,
        targets: Sequence[str],
    ) -> Sequence[SignalCandidate]:
        if not targets:
            return ()
        result = await session.execute(
            DOCUMENT_CANDIDATES,
            {
                "targets": list(targets),
                "direction": move.direction,
                "start": window.start,
                "end": window.end,
                "limit": self.limit,
            },
        )
        rows = sorted(result.all(), key=lambda row: (row[4], str(row[0])))
        return tuple(
            SignalCandidate(
                ref=f"D{index}",
                source_type=self.source_type,
                source_id=row[0],
                source_version=int(row[1]),
                title=str(row[2]),
                publisher=row[3],
                published_at=row[4],
                scope=str(row[5]),
                timing=timing_of(row[4], move),
            )
            for index, row in enumerate(rows, start=1)
        )


@dataclass(frozen=True)
class EventCandidateSource:
    """Structured events (7). Phase 2 adds target price and flow queries here."""

    source_type: SourceType = "EVENT"

    async def candidates(
        self,
        session: AsyncSession,
        move: OpenMove,
        window: CandidateRange,
        targets: Sequence[str],
    ) -> Sequence[SignalCandidate]:
        _ = (session, move, window, targets)
        return ()


CANDIDATE_SOURCES: tuple[CandidateSource, ...] = (
    DocumentCandidateSource(),
    EventCandidateSource(),
)


async def target_symbols(session: AsyncSession, move: OpenMove) -> tuple[str, ...]:
    _ = session
    return (move.symbol,)


async def collect_candidates(
    session: AsyncSession,
    move: OpenMove,
    window: CandidateRange,
    sources: Sequence[CandidateSource] = CANDIDATE_SOURCES,
) -> CandidateSet:
    targets = await target_symbols(session, move)
    documents: list[SignalCandidate] = []
    events: list[SignalCandidate] = []
    for source in sources:
        found = await source.candidates(session, move, window, targets)
        (documents if source.source_type == "DOCUMENT" else events).extend(found)
    return CandidateSet(documents=tuple(documents), events=tuple(events))

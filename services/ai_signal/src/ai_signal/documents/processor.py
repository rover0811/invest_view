"""Document processor: ``news-raw`` to bronze, silver, judgement and signal requests.

Per message, inside the frame's single transaction:

1. ``bronze.news_title_raw``: insert once per ``idempotency_key``
2. ``silver.document`` (+ ``document_kis_news``, ``document_revision``): a new
   title for a known ``source_doc_id`` raises ``version`` and adds a revision;
   a replayed older title changes nothing
3. ``silver.document_entity``: every method in ``LINK_METHODS``
4. ``silver.document_judgement``: only when a linked symbol has
   ``signal_enabled`` and the current version has no judgement yet. Documents
   without an enabled symbol get no judgement row, which is the "not judged"
   state (``direction`` null in 6.4). When every fallback step passes the
   document also stays unjudged.
5. a judgement without ``fail_reason``, with probability >= 0.7 and a
   direction equal to the symbol's latest move state publishes ``NEW_DOCUMENT``
   signal requests (event time = ``published_at``) through the outbox
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.consumers.frame import ConsumedMessage, HandlerContext
from ai_signal.documents.judgement import (
    Candidate,
    DocumentJudge,
    Judgement,
    JudgementRequest,
)
from ai_signal.documents.linking import ENTITY_SYMBOL, LINK_METHODS, LinkMethod
from ai_signal.documents.news import DOCUMENT_SOURCE, NewsRecord
from ai_signal.llm import shared_client
from ai_signal.signals.move import DIRECTION_BY_PRICE_STATE, latest_move
from ai_signal.signal_requests import (
    RequestTrigger,
    SignalRequestReason,
    publish_signal_requests,
    request_targets,
)

logger = logging.getLogger(__name__)

NEW_DOCUMENT_MIN_PROBABILITY = 0.7
UUID_T = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)

NEWS_TITLE_RAW = sa.table(
    "news_title_raw",
    sa.column("raw_id", UUID_T),
    sa.column("idempotency_key", sa.Text()),
    sa.column("source_doc_id", sa.Text()),
    sa.column("symbol", sa.Text()),
    sa.column("provider_code", sa.Text()),
    sa.column("category_code", sa.Text()),
    sa.column("title", sa.Text()),
    sa.column("published_at", TS),
    sa.column("observed_at", TS),
    sa.column("received_at", TS),
    sa.column("source_payload", postgresql.JSONB()),
    schema="bronze",
)
DOCUMENT = sa.table(
    "document",
    sa.column("document_id", UUID_T),
    sa.column("source", sa.Text()),
    sa.column("source_doc_id", sa.Text()),
    sa.column("title", sa.Text()),
    sa.column("publisher", sa.Text()),
    sa.column("published_at", TS),
    sa.column("published_precision", sa.Text()),
    sa.column("known_at", TS),
    sa.column("dedup_key", sa.Text()),
    sa.column("version", sa.Integer()),
    schema="silver",
)
DOCUMENT_KIS_NEWS = sa.table(
    "document_kis_news",
    sa.column("document_id", UUID_T),
    sa.column("provider_code", sa.Text()),
    sa.column("category_code", sa.Text()),
    sa.column("raw_id", UUID_T),
    schema="silver",
)
DOCUMENT_REVISION = sa.table(
    "document_revision",
    sa.column("document_id", UUID_T),
    sa.column("version", sa.Integer()),
    sa.column("title", sa.Text()),
    sa.column("observed_at", TS),
    schema="silver",
)
DOCUMENT_ENTITY = sa.table(
    "document_entity",
    sa.column("document_id", UUID_T),
    sa.column("entity_id", UUID_T),
    sa.column("link_method", sa.Text()),
    sa.column("link_confidence", sa.Numeric(5, 4)),
    schema="silver",
)
DOCUMENT_JUDGEMENT = sa.table(
    "document_judgement",
    sa.column("document_id", UUID_T),
    sa.column("version", sa.Integer()),
    sa.column("direction", sa.SmallInteger()),
    sa.column("scope", sa.Text()),
    sa.column("fail_reason", sa.Text()),
    sa.column("decided_by", sa.Text()),
    sa.column("model_version", sa.Text()),
    sa.column("prompt_version", sa.Text()),
    sa.column("probability", sa.Numeric(5, 4)),
    sa.column("llm_attempts", postgresql.JSONB()),
    schema="silver",
)
ENTITY = sa.table(
    "entity",
    sa.column("entity_id", UUID_T),
    sa.column("name", sa.Text()),
    schema="reference",
)
ENTITY_RELATION = sa.table(
    "entity_relation",
    sa.column("from_entity_id", UUID_T),
    sa.column("to_entity_id", UUID_T),
    sa.column("relation_type", sa.Text()),
    sa.column("valid_to", TS),
    schema="reference",
)


@dataclass(frozen=True)
class DocumentState:
    document_id: UUID
    version: int
    title: str
    publisher: str | None
    published_at: datetime


@dataclass(frozen=True)
class LinkedSymbol:
    entity_id: UUID
    ticker: str
    name: str
    signal_enabled: bool


async def insert_raw(session: AsyncSession, record: NewsRecord) -> None:
    _ = await session.execute(
        postgresql.insert(NEWS_TITLE_RAW)
        .values(
            raw_id=record.raw_id,
            idempotency_key=record.idempotency_key,
            source_doc_id=record.source_doc_id,
            symbol=record.symbol,
            provider_code=record.provider_code,
            category_code=record.category_code,
            title=record.title,
            published_at=record.published_at,
            observed_at=record.observed_at,
            received_at=record.received_at,
            source_payload=dict(record.source_payload),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )


async def _insert_revision(
    session: AsyncSession, record: NewsRecord, version: int
) -> None:
    _ = await session.execute(
        postgresql.insert(DOCUMENT_REVISION)
        .values(
            document_id=record.document_id,
            version=version,
            title=record.title,
            observed_at=record.observed_at,
        )
        .on_conflict_do_nothing(index_elements=["document_id", "version"])
    )


async def upsert_document(session: AsyncSession, record: NewsRecord) -> DocumentState:
    inserted = await session.execute(
        postgresql.insert(DOCUMENT)
        .values(
            document_id=record.document_id,
            source=DOCUMENT_SOURCE,
            source_doc_id=record.source_doc_id,
            title=record.title,
            publisher=record.publisher,
            published_at=record.published_at,
            published_precision="SECOND",
            known_at=record.observed_at,
            dedup_key=record.dedup_key,
            version=1,
        )
        .on_conflict_do_nothing(index_elements=["document_id"])
        .returning(DOCUMENT.c.document_id)
    )
    if inserted.all():
        _ = await session.execute(
            postgresql.insert(DOCUMENT_KIS_NEWS)
            .values(
                document_id=record.document_id,
                provider_code=record.provider_code,
                category_code=record.category_code,
                raw_id=record.raw_id,
            )
            .on_conflict_do_nothing(index_elements=["document_id"])
        )
        await _insert_revision(session, record, 1)
        return DocumentState(
            record.document_id, 1, record.title, record.publisher, record.published_at
        )

    current = await session.execute(
        sa.select(
            DOCUMENT.c.version,
            DOCUMENT.c.title,
            DOCUMENT.c.publisher,
            DOCUMENT.c.published_at,
        )
        .where(DOCUMENT.c.document_id == record.document_id)
        .with_for_update()
    )
    version, title, publisher, published_at = current.all()[0]
    state = DocumentState(record.document_id, version, title, publisher, published_at)
    if title == record.title:
        return state
    seen = await session.execute(
        sa.select(DOCUMENT_REVISION.c.version).where(
            DOCUMENT_REVISION.c.document_id == record.document_id,
            DOCUMENT_REVISION.c.title == record.title,
        )
    )
    if seen.all():
        return state

    next_version = int(version) + 1
    _ = await session.execute(
        sa.update(DOCUMENT)
        .where(DOCUMENT.c.document_id == record.document_id)
        .values(title=record.title, dedup_key=record.dedup_key, version=next_version)
    )
    _ = await session.execute(
        sa.update(DOCUMENT_KIS_NEWS)
        .where(DOCUMENT_KIS_NEWS.c.document_id == record.document_id)
        .values(raw_id=record.raw_id)
    )
    await _insert_revision(session, record, next_version)
    return DocumentState(
        record.document_id, next_version, record.title, publisher, published_at
    )


async def link_entities(
    session: AsyncSession,
    record: NewsRecord,
    methods: Sequence[LinkMethod] = LINK_METHODS,
) -> None:
    for method in methods:
        for link in await method.link(session, record):
            _ = await session.execute(
                postgresql.insert(DOCUMENT_ENTITY)
                .values(
                    document_id=record.document_id,
                    entity_id=link.entity_id,
                    link_method=link.link_method,
                    link_confidence=link.link_confidence,
                )
                .on_conflict_do_nothing(index_elements=["document_id", "entity_id"])
            )


async def linked_symbols(
    session: AsyncSession, document_id: UUID
) -> tuple[LinkedSymbol, ...]:
    result = await session.execute(
        sa.select(
            ENTITY_SYMBOL.c.entity_id,
            ENTITY_SYMBOL.c.ticker,
            ENTITY.c.name,
            ENTITY_SYMBOL.c.signal_enabled,
        )
        .select_from(
            DOCUMENT_ENTITY.join(
                ENTITY_SYMBOL, ENTITY_SYMBOL.c.entity_id == DOCUMENT_ENTITY.c.entity_id
            ).join(ENTITY, ENTITY.c.entity_id == ENTITY_SYMBOL.c.entity_id)
        )
        .where(DOCUMENT_ENTITY.c.document_id == document_id)
        .order_by(ENTITY_SYMBOL.c.ticker)
    )
    return tuple(
        LinkedSymbol(row[0], str(row[1]), str(row[2]), bool(row[3]))
        for row in result.all()
    )


async def sector_names(
    session: AsyncSession, entity_ids: Sequence[UUID]
) -> dict[UUID, tuple[str, ...]]:
    result = await session.execute(
        sa.select(ENTITY_RELATION.c.from_entity_id, ENTITY.c.name)
        .select_from(
            ENTITY_RELATION.join(
                ENTITY, ENTITY.c.entity_id == ENTITY_RELATION.c.to_entity_id
            )
        )
        .where(
            ENTITY_RELATION.c.from_entity_id.in_(list(entity_ids)),
            ENTITY_RELATION.c.relation_type == "BELONGS_TO",
            ENTITY_RELATION.c.valid_to.is_(None),
        )
        .order_by(ENTITY.c.name)
    )
    sectors: dict[UUID, list[str]] = {}
    for entity_id, name in result.all():
        sectors.setdefault(entity_id, []).append(str(name))
    return {key: tuple(names) for key, names in sectors.items()}


async def move_direction(session: AsyncSession, symbol: str) -> int:
    """Direction of the symbol's latest move state; 0 when NEUTRAL or no move."""
    move = await latest_move(session, symbol)
    if move is None:
        return 0
    return DIRECTION_BY_PRICE_STATE.get(move.price_state, 0)


async def judgement_exists(
    session: AsyncSession, document_id: UUID, version: int
) -> bool:
    result = await session.execute(
        sa.select(DOCUMENT_JUDGEMENT.c.version).where(
            DOCUMENT_JUDGEMENT.c.document_id == document_id,
            DOCUMENT_JUDGEMENT.c.version == version,
        )
    )
    return bool(result.all())


async def insert_judgement(
    session: AsyncSession, state: DocumentState, judgement: Judgement
) -> None:
    output = judgement.output
    _ = await session.execute(
        postgresql.insert(DOCUMENT_JUDGEMENT)
        .values(
            document_id=state.document_id,
            version=state.version,
            direction=output.direction,
            scope=output.scope,
            fail_reason=output.fail_reason,
            decided_by=judgement.decided_by,
            model_version=judgement.model_version,
            prompt_version=judgement.prompt_version,
            probability=Decimal(str(round(output.probability, 4))),
            llm_attempts=judgement.llm_attempts,
        )
        .on_conflict_do_nothing(index_elements=["document_id", "version"])
    )


def default_judge() -> DocumentJudge:
    return DocumentJudge.from_client(shared_client())


class DocumentProcessor:
    """``Handler`` for ``news-raw``; the judge is built on first use."""

    def __init__(
        self,
        judge_factory: Callable[[], DocumentJudge] = default_judge,
        link_methods: Sequence[LinkMethod] = LINK_METHODS,
    ) -> None:
        self._judge_factory = judge_factory
        self._judge: DocumentJudge | None = None
        self._link_methods = tuple(link_methods)

    def _get_judge(self) -> DocumentJudge:
        if self._judge is None:
            self._judge = self._judge_factory()
        return self._judge

    async def __call__(self, message: ConsumedMessage, context: HandlerContext) -> None:
        session = context.session
        record = NewsRecord.from_message(message)
        await insert_raw(session, record)
        state = await upsert_document(session, record)
        await link_entities(session, record, self._link_methods)

        enabled = [
            s
            for s in await linked_symbols(session, state.document_id)
            if s.signal_enabled
        ]
        if not enabled:
            return
        if await judgement_exists(session, state.document_id, state.version):
            return

        sectors = await sector_names(session, [s.entity_id for s in enabled])
        request = JudgementRequest(
            title=state.title,
            publisher=state.publisher,
            published_at=record.published_at,
            candidates=tuple(
                Candidate(s.ticker, s.name, sectors.get(s.entity_id, ()))
                for s in enabled
            ),
        )
        judgement = await self._get_judge().judge(request)
        if judgement is None:
            logger.warning(
                "document %s v%s left unjudged: every fallback step passed",
                state.document_id,
                state.version,
            )
            return
        await insert_judgement(session, state, judgement)
        output = judgement.output
        if (
            not output.is_signal_candidate
            or output.probability < NEW_DOCUMENT_MIN_PROBABILITY
            or not output.direction
        ):
            return
        targets = await request_targets(
            session,
            RequestTrigger(
                scope=judgement.output.scope, symbols=tuple(s.ticker for s in enabled)
            ),
        )
        aligned = [
            symbol
            for symbol in targets
            if await move_direction(session, symbol) == output.direction
        ]
        _ = await publish_signal_requests(
            context.outbox,
            aligned,
            SignalRequestReason.NEW_DOCUMENT,
            record.published_at,
        )

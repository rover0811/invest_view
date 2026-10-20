"""Document to entity link methods (6.2, 18.10 "가공").

``LINK_METHODS`` is the registered list. Phase 1 has ``source_tag`` only; phase 2
adds a ``title_alias`` method (6.3) as a new class and one line in the list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.documents.news import NewsRecord

ENTITY_SYMBOL = sa.table(
    "entity_symbol",
    sa.column("entity_id", postgresql.UUID(as_uuid=True)),
    sa.column("ticker", sa.Text()),
    sa.column("market", sa.Text()),
    sa.column("signal_enabled", sa.Boolean()),
    schema="reference",
)


@dataclass(frozen=True)
class EntityLink:
    entity_id: UUID
    link_method: str
    link_confidence: Decimal


class LinkMethod(Protocol):
    @property
    def name(self) -> str: ...

    async def link(
        self, session: AsyncSession, record: NewsRecord
    ) -> Sequence[EntityLink]: ...


class SourceTagLink:
    """The symbol the provider tagged the headline with (``news-raw`` key)."""

    name: str = "source_tag"

    async def link(
        self, session: AsyncSession, record: NewsRecord
    ) -> Sequence[EntityLink]:
        result = await session.execute(
            sa.select(ENTITY_SYMBOL.c.entity_id).where(
                ENTITY_SYMBOL.c.ticker == record.symbol
            )
        )
        return [
            EntityLink(
                entity_id=row[0], link_method=self.name, link_confidence=Decimal(1)
            )
            for row in result.all()
        ]


LINK_METHODS: tuple[LinkMethod, ...] = (SourceTagLink(),)

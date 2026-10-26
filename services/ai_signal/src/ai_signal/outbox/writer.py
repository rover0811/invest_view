"""Outbox writer: the only way ai_signal publishes to ``signal-requests`` and ``signal-updates``.

Rows follow the Debezium Outbox Event Router table layout (16.4, 18.6):

- ``aggregatetype``: destination topic name (router ``route.by.field``)
- ``aggregateid``: Kafka message key (the symbol for every AI signal topic)
- ``type``: event type, the Avro record name of the topic value schema
- ``payload``: Schema Registry Avro bytes, passed through unchanged

The writer only executes an INSERT on the caller's session, so the event commits
or rolls back together with the data tables written in the same transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

OUTBOX_EVENTS = sa.table(
    "events",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("aggregatetype", sa.Text()),
    sa.column("aggregateid", sa.Text()),
    sa.column("type", sa.Text()),
    sa.column("payload", sa.LargeBinary()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    schema="outbox",
)


class ValueSerializer(Protocol):
    def serialize(self, topic: str, value: Mapping[str, object]) -> bytes: ...

    def record_name(self, topic: str) -> str: ...


class OutboxWriter:
    def __init__(self, session: AsyncSession, serializer: ValueSerializer) -> None:
        self._session = session
        self._serializer = serializer

    async def write(
        self,
        *,
        topic: str,
        key: str,
        value: Mapping[str, object],
        event_type: str | None = None,
        event_id: UUID | None = None,
    ) -> UUID:
        """Insert one event; a caller-supplied ``event_id`` makes retries no-ops."""
        resolved_id = event_id if event_id is not None else uuid4()
        statement = (
            postgresql.insert(OUTBOX_EVENTS)
            .values(
                id=resolved_id,
                aggregatetype=topic,
                aggregateid=key,
                type=event_type or self._serializer.record_name(topic),
                payload=self._serializer.serialize(topic, value),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        _ = await self._session.execute(statement)
        return resolved_id

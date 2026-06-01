"""TickHistory repository — append-only bronze insert with Kafka-identity dedupe."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.db.models import TickHistory
from tick_persistence.kafka.consumer import TickMessage

_META_COLUMNS = frozenset(
    {"tick_id", "persisted_at", "kafka_topic", "kafka_partition", "kafka_offset", "tick_dedupe_key"}
)
_KIS_COLUMNS: tuple[str, ...] = tuple(
    column.name for column in TickHistory.__table__.columns if column.name not in _META_COLUMNS
)


class TickHistoryRepository:
    async def insert(self, session: AsyncSession, message: TickMessage) -> bool:
        """Append the raw tick to bronze. Returns True if inserted, False if a duplicate Kafka message was skipped."""
        dedupe_key = f"{message.topic}:{message.partition}:{message.offset}"
        values: dict[str, Any] = {name: message.value.get(name) for name in _KIS_COLUMNS}
        values["tick_id"] = uuid.uuid4()
        values["kafka_topic"] = message.topic
        values["kafka_partition"] = message.partition
        values["kafka_offset"] = message.offset
        values["tick_dedupe_key"] = dedupe_key

        stmt = (
            pg_insert(TickHistory)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["tick_dedupe_key"])
            .returning(TickHistory.tick_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

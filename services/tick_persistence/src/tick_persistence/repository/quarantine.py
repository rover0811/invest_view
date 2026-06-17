"""TickQuarantine repository — durable, idempotent isolation of poison-pill ticks."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.db.models import TickQuarantine


@dataclass(frozen=True)
class QuarantinedTick:
    payload: dict[str, Any]
    topic: str
    partition: int
    offset: int
    reason: str


def _json_safe(payload: Mapping[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(json.dumps(dict(payload), default=str)))


class QuarantineRepository:
    def __init__(self) -> None:
        self._quarantined_count = 0

    @property
    def quarantined_count(self) -> int:
        return self._quarantined_count

    async def quarantine_many(self, session: AsyncSession, entries: list[QuarantinedTick]) -> int:
        if not entries:
            return 0

        values = [
            {
                "raw_payload": _json_safe(entry.payload),
                "kafka_topic": entry.topic,
                "kafka_partition": entry.partition,
                "kafka_offset": entry.offset,
                "reason": entry.reason,
            }
            for entry in entries
        ]
        stmt = (
            pg_insert(TickQuarantine)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=["kafka_topic", "kafka_partition", "kafka_offset"]
            )
            .returning(TickQuarantine.id)
        )
        result = await session.execute(stmt)
        inserted = len(result.scalars().all())
        self._quarantined_count += inserted
        return inserted

"""TickHistory repository — append-only bronze insert with deterministic event_id dedupe."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.db.models import TickHistory
from tick_persistence.event_id import compute_event_id
from tick_persistence.kafka.consumer import TickMessage

KST = ZoneInfo("Asia/Seoul")
_POSTGRES_BIND_PARAM_BUDGET = 60_000
_ASYNCPG_BIND_PARAM_BUDGET = 30_000

_META_COLUMNS = frozenset(
    {
        "tick_id",
        "persisted_at",
        "kafka_topic",
        "kafka_partition",
        "kafka_offset",
        "tick_dedupe_key",
        "event_id",
        "event_ts",
    }
)
_KIS_COLUMNS: tuple[str, ...] = tuple(
    name
    for column in TickHistory.__table__.columns
    for name in (cast(str, column.name),)
    if name not in _META_COLUMNS
)
_INSERT_COLUMN_COUNT = len(_KIS_COLUMNS) + 7


@dataclass(frozen=True)
class InsertedTick:
    value: dict[str, Any]
    event_id: str
    event_ts: datetime
    partition: int
    offset: int
    market: str | None


def max_insert_rows() -> int:
    return max(1, min(_POSTGRES_BIND_PARAM_BUDGET, _ASYNCPG_BIND_PARAM_BUDGET) // _INSERT_COLUMN_COUNT)


def postgres_max_insert_rows() -> int:
    return max(1, _POSTGRES_BIND_PARAM_BUDGET // _INSERT_COLUMN_COUNT)


def _event_id_for(tick: Mapping[str, object]) -> str:
    """Use producer-provided payload event_id when present; compute the T2 UUIDv5 fallback otherwise."""
    payload_event_id = tick.get("event_id")
    if payload_event_id is not None:
        event_id = str(payload_event_id).strip()
        if event_id:
            return event_id
    return compute_event_id(tick)


def _event_ts_for(tick: Mapping[str, object]) -> datetime:
    business_date = str(tick["business_date"]).strip()
    trade_time = str(tick["trade_time"]).strip()
    return datetime.strptime(f"{business_date}{trade_time}", "%Y%m%d%H%M%S").replace(tzinfo=KST)


def _to_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise TypeError(f"unsupported received_at value: {value!r} ({type(value)})")


class TickHistoryRepository:
    async def insert(self, session: AsyncSession, message: TickMessage) -> bool:
        """Append the raw tick to bronze. Returns True if inserted, False if a duplicate event_id was skipped."""
        return bool(await self.insert_many(session, [message]))

    async def insert_many(self, session: AsyncSession, messages: list[TickMessage]) -> list[InsertedTick]:
        """Append raw ticks to bronze in chunks and return only rows inserted by Postgres."""
        inserted: list[InsertedTick] = []
        for start in range(0, len(messages), max_insert_rows()):
            chunk = messages[start : start + max_insert_rows()]
            inserted.extend(await self._insert_chunk(session, chunk))
        return inserted

    async def _insert_chunk(self, session: AsyncSession, messages: list[TickMessage]) -> list[InsertedTick]:
        if not messages:
            return []

        values = [_values_for(message) for message in messages]
        by_lineage = {(message.partition, message.offset): message for message in messages}

        stmt = (
            pg_insert(TickHistory)
            .values(values)
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(
                TickHistory.event_id,
                TickHistory.event_ts,
                TickHistory.kafka_partition,
                TickHistory.kafka_offset,
            )
        )
        result = await session.execute(stmt)
        inserted: list[InsertedTick] = []
        for event_id, event_ts, partition, offset in result.tuples().all():
            if event_id is None or event_ts is None or partition is None or offset is None:
                continue
            message = by_lineage[(int(partition), int(offset))]
            inserted.append(
                InsertedTick(
                    value=message.value,
                    event_id=str(event_id),
                    event_ts=event_ts,
                    partition=int(partition),
                    offset=int(offset),
                    market=str(message.value["market"]) if message.value.get("market") is not None else None,
                )
            )
        return inserted


def _values_for(message: TickMessage) -> dict[str, object]:
    dedupe_key = f"{message.topic}:{message.partition}:{message.offset}"
    values: dict[str, object] = {name: message.value.get(name) for name in _KIS_COLUMNS}
    values["tick_id"] = uuid.uuid4()
    values["kafka_topic"] = message.topic
    values["kafka_partition"] = message.partition
    values["kafka_offset"] = message.offset
    values["tick_dedupe_key"] = dedupe_key
    values["event_id"] = _event_id_for(message.value)
    values["event_ts"] = _event_ts_for(message.value)
    values["received_at"] = _to_datetime(values.get("received_at"))
    return values

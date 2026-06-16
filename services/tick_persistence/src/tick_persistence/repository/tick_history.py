"""TickHistory repository — append-only bronze insert with deterministic event_id dedupe."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.db.models import TickHistory
from tick_persistence.event_id import compute_event_id
from tick_persistence.kafka.consumer import TickMessage

KST = ZoneInfo("Asia/Seoul")

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

        stmt = (
            pg_insert(TickHistory)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(TickHistory.tick_id)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

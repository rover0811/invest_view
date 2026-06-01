"""PatternEvent repository — idempotent insert of deserialized stock-pattern dicts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from event_pattern_persistence.db.models import PatternEvent


def _to_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _to_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    # confluent/fastavro decodes timestamp-millis to a tz-aware datetime; raw test
    # dicts carry epoch-millis ints. Accept both and normalise to UTC.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    raise TypeError(f"unsupported timestamp value: {value!r} ({type(value)})")


def _to_row(pattern: dict[str, Any]) -> dict[str, Any]:
    return {
        "pattern_event_id": _to_uuid(pattern["pattern_event_id"]),
        "symbol": pattern["symbol"],
        "market": pattern.get("market"),
        "pattern_type": pattern["pattern_type"],
        "window_start": _to_utc_datetime(pattern.get("window_start")),
        "window_end": _to_utc_datetime(pattern.get("window_end")),
        "triggered_at": _to_utc_datetime(pattern["triggered_at"]),
        "trigger_values": dict(pattern.get("trigger_values") or {}),
        "strategy_name": pattern.get("strategy_name"),
        "source_tick_event_id": pattern.get("source_tick_event_id"),
    }


class PatternEventRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def insert(self, pattern: dict[str, Any]) -> None:
        """Insert pattern if new; duplicate pattern_event_id is a no-op (UUIDv5 idempotency)."""
        row = _to_row(pattern)
        async with self._sf() as session:
            stmt = (
                pg_insert(PatternEvent)
                .values(**row)
                .on_conflict_do_nothing(index_elements=["pattern_event_id"])
            )
            await session.execute(stmt)
            await session.commit()

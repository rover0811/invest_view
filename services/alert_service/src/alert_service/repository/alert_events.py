"""AlertEvent repository — idempotent upsert + lookup."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.db.models import AlertEvent


class AlertEventRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def upsert(self, event: dict[str, Any]) -> bool:
        """Insert event if new. Returns True if a new row was inserted, False if duplicate."""
        async with self._sf() as session:
            stmt = (
                pg_insert(AlertEvent)
                .values(**event)
                .on_conflict_do_nothing(index_elements=["alert_event_id"])
                .returning(AlertEvent.alert_event_id)
            )
            result = await session.execute(stmt)
            inserted = result.scalar_one_or_none()
            await session.commit()
            return inserted is not None

    async def get(self, alert_event_id: uuid.UUID) -> AlertEvent | None:
        async with self._sf() as session:
            return await session.get(AlertEvent, alert_event_id)

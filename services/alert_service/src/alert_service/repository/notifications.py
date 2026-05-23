"""Notification repository — bulk creation + delivery status updates + pagination."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.db.models import NotificationEvent


class NotificationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def bulk_create_pending(
        self,
        user_ids: list[uuid.UUID],
        alert_event_id: uuid.UUID,
        symbol: str,
    ) -> list[uuid.UUID]:
        """Insert one PENDING notification per user_id for the given alert.

        On (user_id, alert_event_id) conflict, the duplicate is skipped (idempotent
        replay safety). Returns the notification_ids of the newly inserted rows.
        """
        if not user_ids:
            return []
        rows: list[dict[str, Any]] = [
            {
                "notification_id": uuid.uuid4(),
                "user_id": uid,
                "alert_event_id": alert_event_id,
                "symbol": symbol,
                "delivery_status": "PENDING",
            }
            for uid in user_ids
        ]
        async with self._sf() as session:
            stmt = (
                pg_insert(NotificationEvent)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "alert_event_id"]
                )
                .returning(NotificationEvent.notification_id)
            )
            result = await session.execute(stmt)
            inserted = [row[0] for row in result.all()]
            await session.commit()
            return inserted

    async def mark_sent(self, notification_id: uuid.UUID, delivered_at: datetime) -> None:
        async with self._sf() as session:
            await session.execute(
                update(NotificationEvent)
                .where(NotificationEvent.notification_id == notification_id)
                .values(
                    delivery_status="SENT",
                    delivery_attempted_at=delivered_at,
                    delivered_at=delivered_at,
                )
            )
            await session.commit()

    async def mark_failed(
        self,
        notification_id: uuid.UUID,
        attempted_at: datetime,
        reason: str,
    ) -> None:
        async with self._sf() as session:
            await session.execute(
                update(NotificationEvent)
                .where(NotificationEvent.notification_id == notification_id)
                .values(
                    delivery_status="FAILED",
                    delivery_attempted_at=attempted_at,
                    failure_reason=reason,
                )
            )
            await session.commit()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        since: datetime | None,
        limit: int,
    ) -> list[NotificationEvent]:
        async with self._sf() as session:
            stmt = select(NotificationEvent).where(NotificationEvent.user_id == user_id)
            if since is not None:
                stmt = stmt.where(NotificationEvent.created_at > since)
            stmt = stmt.order_by(NotificationEvent.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

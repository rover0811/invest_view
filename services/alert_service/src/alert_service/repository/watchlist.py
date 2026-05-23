"""Watchlist repository.

Includes:
- list_for_user / add / remove / set_notifications_enabled (user-facing CRUD)
- find_users_for_symbol (fanout: who watches this symbol with notifications on?)
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.db.models import WatchlistItem


class WatchlistDuplicateError(Exception):
    """Raised when (user_id, symbol) already exists."""


class WatchlistRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def list_for_user(self, user_id: uuid.UUID) -> list[WatchlistItem]:
        async with self._sf() as session:
            result = await session.execute(
                select(WatchlistItem)
                .where(WatchlistItem.user_id == user_id)
                .order_by(WatchlistItem.created_at)
            )
            return list(result.scalars().all())

    async def add(self, user_id: uuid.UUID, symbol: str) -> WatchlistItem:
        async with self._sf() as session:
            item = WatchlistItem(user_id=user_id, symbol=symbol)
            session.add(item)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise WatchlistDuplicateError(
                    f"watchlist already has user_id={user_id} symbol={symbol}"
                ) from exc
            await session.refresh(item)
            return item

    async def remove(self, user_id: uuid.UUID, symbol: str) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                delete(WatchlistItem).where(
                    (WatchlistItem.user_id == user_id) & (WatchlistItem.symbol == symbol)
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def set_notifications_enabled(
        self, user_id: uuid.UUID, symbol: str, enabled: bool
    ) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                update(WatchlistItem)
                .where((WatchlistItem.user_id == user_id) & (WatchlistItem.symbol == symbol))
                .values(notifications_enabled=enabled)
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def find_users_for_symbol(self, symbol: str) -> list[uuid.UUID]:
        """Return user_ids that watch ``symbol`` AND have notifications enabled."""
        async with self._sf() as session:
            result = await session.execute(
                select(WatchlistItem.user_id)
                .where(WatchlistItem.symbol == symbol)
                .where(WatchlistItem.notifications_enabled.is_(True))
            )
            return [row[0] for row in result.all()]

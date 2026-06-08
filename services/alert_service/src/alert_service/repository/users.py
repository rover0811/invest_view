"""User repository."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alert_service.db.models import User


class UserRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get(self, user_id: uuid.UUID) -> User | None:
        async with self._sf() as session:
            return await session.get(User, user_id)

    async def exists(self, user_id: uuid.UUID) -> bool:
        async with self._sf() as session:
            result = await session.execute(
                select(User.user_id).where(User.user_id == user_id)
            )
            return result.scalar_one_or_none() is not None

    async def get_or_create_by_nickname(self, nickname: str) -> User:
        async with self._sf() as session:
            result = await session.execute(
                select(User).where(User.nickname == nickname).limit(1)
            )
            user = result.scalar_one_or_none()
            if user is not None:
                return user

            user = User(user_id=uuid.uuid4(), nickname=nickname)
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

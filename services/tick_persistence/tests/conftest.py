import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _async_url(raw: str) -> str:
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


async def _create_signal_timeline_deps_async(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS alert_service"))
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS alert_service.alert_events (
                    alert_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    symbol TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    triggered_at TIMESTAMPTZ NOT NULL,
                    trigger_values JSONB NOT NULL,
                    severity TEXT NOT NULL
                )
                """
            )
        )
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS gold"))
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS gold.pattern_events (
                    pattern_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    symbol TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    triggered_at TIMESTAMPTZ NOT NULL,
                    trigger_values JSONB NOT NULL
                )
                """
            )
        )
    await engine.dispose()


def _create_signal_timeline_deps(async_url: str) -> None:
    asyncio.run(_create_signal_timeline_deps_async(async_url))


@pytest.fixture(scope="session")
def postgres_container():
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="function")
def migrated_url(postgres_container) -> Iterator[str]:
    from alembic import command
    from alembic.config import Config

    url = _async_url(postgres_container.get_connection_url())
    previous = os.environ.get("TICK_PERSISTENCE_DATABASE_URL")
    os.environ["TICK_PERSISTENCE_DATABASE_URL"] = url

    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))

    _create_signal_timeline_deps(url)
    command.upgrade(cfg, "head")
    try:
        yield url
    finally:
        command.downgrade(cfg, "base")
        if previous is None:
            os.environ.pop("TICK_PERSISTENCE_DATABASE_URL", None)
        else:
            os.environ["TICK_PERSISTENCE_DATABASE_URL"] = previous


@pytest_asyncio.fixture(scope="function")
async def db_session_factory(migrated_url) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from tick_persistence.db.session import create_engine, create_session_factory

    engine = create_engine(migrated_url)
    yield create_session_factory(engine)
    await engine.dispose()

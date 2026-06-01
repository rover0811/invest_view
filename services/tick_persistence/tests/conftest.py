import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _async_url(raw: str) -> str:
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


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

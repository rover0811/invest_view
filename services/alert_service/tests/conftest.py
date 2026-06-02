"""Test fixtures for alert_service tests.

Specific fixtures (postgres_container, mock_kafka_producer, etc.) are
added by their introducing tasks (T6/T9/T7/T8/T11). T1 only provides
the src-layout sys.path bootstrap so test modules can ``from alert_service.* import``
without requiring an editable install (matches the kis_ingestion test pattern).
"""
import sys
from pathlib import Path

# src layout: make ``services/alert_service/src/`` importable so tests can
# do ``from alert_service.config import ...`` regardless of pytest CWD.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402


@pytest.fixture(scope="session")
def postgres_container():
    """testcontainers-managed Postgres for repository QA tests."""
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    yield container
    container.stop()


@pytest_asyncio.fixture(scope="function")
async def db_engine(postgres_container):
    from alert_service.db.session import create_engine
    from alert_service.db.models import Base

    url = (
        postgres_container.get_connection_url()
        .replace("postgresql+psycopg2", "postgresql+asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS alert_service")
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS alert_service CASCADE")
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    from alert_service.db.session import create_session_factory

    return create_session_factory(db_engine)

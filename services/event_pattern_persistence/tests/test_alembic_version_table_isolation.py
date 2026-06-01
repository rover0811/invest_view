from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


def _asyncpg_url(container: PostgresContainer) -> str:
    return (
        container.get_connection_url()
        .replace("postgresql+psycopg2", "postgresql+asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _alembic_config(service_dir: Path) -> Config:
    cfg = Config(str(service_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(service_dir / "alembic"))
    return cfg


async def _seed_shared_public_alembic_version(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
                """
            )
        )
        await conn.execute(
            text("INSERT INTO public.alembic_version (version_num) VALUES ('0001_initial')")
        )
    await engine.dispose()


async def _fetch_state(url: str) -> tuple[str | None, str | None]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        pattern_events = await conn.scalar(
            text("SELECT to_regclass('gold.pattern_events')")
        )
        version = await conn.scalar(
            text("SELECT version_num FROM event_pattern_persistence_alembic_version")
        )
    await engine.dispose()
    return (str(pattern_events) if pattern_events is not None else None), version


def test_shared_public_alembic_version_does_not_skip_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    cfg = _alembic_config(service_dir)

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("EVENT_PATTERN_PERSISTENCE_DATABASE_URL", url)

        asyncio.run(_seed_shared_public_alembic_version(url))

        command.upgrade(cfg, "head")

        pattern_events, version = asyncio.run(_fetch_state(url))

        assert pattern_events == "gold.pattern_events", (
            "gold.pattern_events must be created even though public.alembic_version "
            "already records 0001_initial"
        )
        assert version == "0001_initial", (
            "event_pattern_persistence_alembic_version must record its own head, "
            f"got {version!r}"
        )

        command.downgrade(cfg, "base")

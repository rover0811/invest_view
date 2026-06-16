from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

_EXPECTED_OBJECTS: tuple[str, ...] = (
    "bronze.tick_history",
    "silver.symbol_5m_metrics",
    "silver.symbol_daily_ohlc",
    "serving.symbol_signal_timeline",
    "serving.symbol_daily_ohlc",
)
_HEAD_REVISION = "0006_tick_event_time_contract"


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


async def _seed_shared_db(url: str) -> None:
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


async def _fetch_state(url: str) -> tuple[set[str], str | None]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        present: set[str] = set()
        for obj in _EXPECTED_OBJECTS:
            regclass = await conn.scalar(text("SELECT to_regclass(:obj)"), {"obj": obj})
            if regclass is not None:
                present.add(obj)
        version = await conn.scalar(
            text("SELECT version_num FROM tick_persistence_alembic_version")
        )
    await engine.dispose()
    return present, version


def test_shared_public_alembic_version_does_not_skip_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    cfg = _alembic_config(service_dir)

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("TICK_PERSISTENCE_DATABASE_URL", url)

        asyncio.run(_seed_shared_db(url))

        command.upgrade(cfg, "head")

        present, version = asyncio.run(_fetch_state(url))

        assert present == set(_EXPECTED_OBJECTS), (
            "tick_persistence migrations must run despite public.alembic_version=0001_initial; "
            f"missing objects: {set(_EXPECTED_OBJECTS) - present}"
        )
        assert version == _HEAD_REVISION, (
            "tick_persistence_alembic_version must record head, " f"got {version!r}"
        )

        command.downgrade(cfg, "base")

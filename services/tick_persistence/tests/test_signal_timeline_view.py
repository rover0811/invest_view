from __future__ import annotations

import asyncio
import uuid
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


async def _create_dependency_stubs(url: str) -> None:
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


async def _query_timeline_counts(url: str, symbol: str) -> dict[str, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT event_kind, count(*)::int
                FROM serving.symbol_signal_timeline
                WHERE symbol = :symbol
                GROUP BY event_kind
                ORDER BY event_kind
                """
            ),
            {"symbol": symbol},
        )
        result = {row[0]: row[1] for row in rows}
    await engine.dispose()
    return result


async def _insert_test_rows(url: str, symbol: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO alert_service.alert_events
                    (alert_event_id, symbol, alert_type, triggered_at, trigger_values, severity)
                VALUES
                    (:id, :symbol, 'PRICE_ALERT', '2026-06-01 09:00:00+00', '{"price": "70000"}'::jsonb, 'INFO')
                """
            ),
            {"id": str(uuid.uuid4()), "symbol": symbol},
        )
        await conn.execute(
            text(
                """
                INSERT INTO gold.pattern_events
                    (pattern_event_id, symbol, pattern_type, triggered_at, trigger_values)
                VALUES
                    (:id, :symbol, 'GOLDEN_CROSS', '2026-06-01 09:05:00+00', '{"ma5": "70100", "ma20": "69900"}'::jsonb)
                """
            ),
            {"id": str(uuid.uuid4()), "symbol": symbol},
        )
    await engine.dispose()


async def _check_view_exists(url: str) -> bool:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        regclass = await conn.scalar(
            text("SELECT to_regclass('serving.symbol_signal_timeline')")
        )
    await engine.dispose()
    return regclass == "serving.symbol_signal_timeline"


def test_signal_timeline_view_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    cfg = _alembic_config(service_dir)

    with PostgresContainer("postgres:16-alpine") as container:
        async_url = _asyncpg_url(container)
        monkeypatch.setenv("TICK_PERSISTENCE_DATABASE_URL", async_url)

        asyncio.run(_create_dependency_stubs(async_url))

        command.upgrade(cfg, "head")

        assert asyncio.run(_check_view_exists(async_url))

        asyncio.run(_insert_test_rows(async_url, "005930"))

        counts = asyncio.run(_query_timeline_counts(async_url, "005930"))
        assert counts == {"alert": 1, "pattern": 1}, f"unexpected counts: {counts}"

        command.downgrade(cfg, "base")


def test_signal_timeline_view_wrong_order_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    cfg = _alembic_config(service_dir)

    with PostgresContainer("postgres:16-alpine") as container:
        async_url = _asyncpg_url(container)
        monkeypatch.setenv("TICK_PERSISTENCE_DATABASE_URL", async_url)

        with pytest.raises(Exception) as exc_info:
            command.upgrade(cfg, "head")

        error_message = str(exc_info.value)
        assert "signal_timeline view requires" in error_message or any(
            "signal_timeline view requires" in str(arg) for arg in exc_info.value.args
        ), f"guard message not found in: {error_message}"

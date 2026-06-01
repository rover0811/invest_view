from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TypedDict

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


class _MigrationState(TypedDict):
    schemas: set[str]
    tables: set[tuple[str, str]]
    silver_columns: set[str]
    constraints: set[str]


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


async def _fetch_migration_state(url: str) -> _MigrationState:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        schemas = set(
            await conn.scalars(
                text(
                    """SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name IN ('bronze','silver','serving')"""
                )
            )
        )
        table_rows = await conn.execute(
            text(
                """SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_schema IN ('bronze','silver','serving')"""
            )
        )
        tables = {(row[0], row[1]) for row in table_rows}
        silver_columns = set(
            await conn.scalars(
                text(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_schema='silver' AND table_name='symbol_5m_metrics'"""
                )
            )
        )
        constraints = set(
            await conn.scalars(
                text(
                    """SELECT conname FROM pg_constraint
                    WHERE conname LIKE '%dedupe%' OR conname LIKE '%symbol_bucket%'"""
                )
            )
        )
    await engine.dispose()
    return {
        "schemas": schemas,
        "tables": tables,
        "silver_columns": silver_columns,
        "constraints": constraints,
    }


async def _fetch_schemas(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        schemas = set(
            await conn.scalars(
                text(
                    """SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name IN ('bronze','silver','serving')"""
                )
            )
        )
    await engine.dispose()
    return schemas


def test_alembic_upgrade_and_downgrade_create_expected_objects(monkeypatch: pytest.MonkeyPatch):
    service_dir = Path(__file__).resolve().parents[1]
    cfg = _alembic_config(service_dir)

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("TICK_PERSISTENCE_DATABASE_URL", url)

        command.upgrade(cfg, "head")
        state = asyncio.run(_fetch_migration_state(url))

        assert state["schemas"] == {"bronze", "silver", "serving"}
        assert state["tables"] == {
            ("bronze", "tick_history"),
            ("silver", "symbol_5m_metrics"),
            ("serving", "symbol_snapshot"),
        }
        assert "is_final" in state["silver_columns"]
        assert "tick_history_dedupe_key_uq" in state["constraints"]
        assert "symbol_5m_metrics_symbol_bucket_uq" in state["constraints"]

        command.downgrade(cfg, "base")
        assert asyncio.run(_fetch_schemas(url)) == set()

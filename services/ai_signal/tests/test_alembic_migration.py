from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Protocol, TypedDict, cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

PostgresContainer = importlib.import_module(
    "testcontainers.community.postgres"
).PostgresContainer

EXPECTED_TABLES = {
    ("reference", "entity"),
    ("reference", "entity_symbol"),
    ("reference", "entity_sector"),
    ("reference", "entity_market"),
    ("reference", "entity_org"),
    ("reference", "entity_index"),
    ("reference", "entity_alias"),
    ("reference", "relation_type_def"),
    ("reference", "entity_relation"),
    ("reference", "index_value"),
    ("reference", "us_etf_daily"),
    ("reference", "index_constituent_weight"),
    ("bronze", "news_title_raw"),
    ("silver", "document"),
    ("silver", "document_kis_news"),
    ("silver", "document_revision"),
    ("silver", "document_entity"),
    ("silver", "document_judgement"),
    ("gold", "event"),
    ("gold", "event_entity"),
    ("gold", "move"),
    ("gold", "symbol_beta"),
    ("gold", "signal"),
    ("gold", "signal_cause"),
    ("gold", "signal_cause_source"),
    ("gold", "signal_retry"),
    ("gold", "signal_decision"),
    ("gold", "llm_cache"),
    ("serving", "symbol_signal"),
    ("serving", "symbol_signal_cause"),
    ("serving", "symbol_signal_cause_source"),
    ("serving", "symbol_signal_history"),
    ("serving", "symbol_signal_history_cause"),
    ("serving", "symbol_signal_history_cause_source"),
    ("outbox", "events"),
}
DEFERRED_TABLES = {
    ("bronze", "flow_raw"),
    ("bronze", "opinion_raw"),
    ("silver", "document_report"),
    ("silver", "flow_observation"),
    ("silver", "flow_baseline"),
    ("gold", "event_target_price"),
    ("gold", "event_flow"),
}


class MigrationState(TypedDict):
    tables: set[tuple[str, str]]
    move_columns: set[str]
    outbox_columns: set[str]
    constraints: set[str]
    symbols: list[tuple[str, bool, str]]
    manual_relation_count: int
    version: str | None


class PostgresContainerProtocol(Protocol):
    def get_connection_url(self) -> str: ...


def _asyncpg_url(container: PostgresContainerProtocol) -> str:
    return (
        container.get_connection_url()
        .replace("postgresql+psycopg2", "postgresql+asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _config(service_dir: Path) -> Config:
    config = Config(str(service_dir / "alembic.ini"))
    config.set_main_option("script_location", str(service_dir / "alembic"))
    return config


async def _inspect(url: str) -> MigrationState:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        tables_result = await connection.execute(
            text(
                """SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_schema IN ('reference','bronze','silver','gold','serving','outbox')
                AND table_type = 'BASE TABLE'"""
            )
        )
        tables = {(str(row[0]), str(row[1])) for row in tables_result}
        move_columns = set(
            await connection.scalars(
                text(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_schema='gold' AND table_name='move'"""
                )
            )
        )
        outbox_columns = set(
            await connection.scalars(
                text(
                    """SELECT column_name FROM information_schema.columns
                    WHERE table_schema='outbox' AND table_name='events'"""
                )
            )
        )
        constraints = set(
            await connection.scalars(
                text(
                    """SELECT conname FROM pg_constraint
                    WHERE conname IN (
                        'document_judgement_version_ck',
                        'document_judgement_info_type_ck',
                        'document_judgement_event_type_ck',
                        'document_judgement_target_relevance_ck',
                        'signal_version_ck',
                        'symbol_signal_version_ck'
                    )"""
                )
            )
        )
        symbols = cast(
            list[tuple[str, bool, str]],
            list(
                await connection.execute(
                    text(
                        """SELECT es.ticker, es.signal_enabled, e.name
                        FROM reference.entity_symbol es
                        JOIN reference.entity e USING (entity_id)
                        ORDER BY es.ticker"""
                    )
                )
            ),
        )
        manual_relation_count = int(
            await connection.scalar(
                text(
                    "SELECT count(*) FROM reference.entity_relation WHERE source='MANUAL'"
                )
            )
            or 0
        )
        version = cast(
            str | None,
            await connection.scalar(
                text("SELECT version_num FROM ai_signal_alembic_version")
            ),
        )
    await engine.dispose()
    return {
        "tables": tables,
        "move_columns": move_columns,
        "outbox_columns": outbox_columns,
        "constraints": constraints,
        "symbols": symbols,
        "manual_relation_count": manual_relation_count,
        "version": version,
    }


def test_upgrade_seeds_and_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    config = _config(service_dir)
    head = ScriptDirectory.from_config(config).get_current_head()

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("AI_SIGNAL_DATABASE_URL", url)

        command.upgrade(config, "head")
        state = asyncio.run(_inspect(url))

        assert state["tables"] == EXPECTED_TABLES
        assert not (state["tables"] & DEFERRED_TABLES)
        assert state["move_columns"] >= {
            "move_type",
            "contrib_global",
            "contrib_market",
            "contrib_sector",
            "contrib_idio",
            "beta_method",
            "dominant_layer",
        }
        assert state["outbox_columns"] >= {
            "id",
            "aggregatetype",
            "aggregateid",
            "type",
            "payload",
            "created_at",
        }
        assert state["constraints"] == {
            "document_judgement_version_ck",
            "document_judgement_info_type_ck",
            "document_judgement_event_type_ck",
            "document_judgement_target_relevance_ck",
            "signal_version_ck",
            "symbol_signal_version_ck",
        }
        symbols = state["symbols"]
        assert len(symbols) == 40
        assert {(ticker, name) for ticker, enabled, name in symbols if enabled} == {
            ("000660", "SK하이닉스"),
            ("005930", "삼성전자"),
        }
        assert state["manual_relation_count"] >= 42
        assert state["version"] == head

        command.downgrade(config, "base")
        assert asyncio.run(_inspect_tables(url)) == set()


async def _inspect_tables(url: str) -> set[tuple[str, str]]:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                """SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_schema IN ('reference','bronze','silver','gold','serving','outbox')
                AND table_type = 'BASE TABLE'"""
            )
        )
        tables = {(row[0], row[1]) for row in rows}
    await engine.dispose()
    return tables


async def _create_shared_object(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE SCHEMA IF NOT EXISTS gold"))
        await connection.execute(
            text("CREATE TABLE gold.preexisting_table (id integer PRIMARY KEY)")
        )
    await engine.dispose()


async def _shared_object_exists(url: str) -> bool:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        exists = await connection.scalar(
            text("SELECT to_regclass('gold.preexisting_table') IS NOT NULL")
        )
    await engine.dispose()
    return bool(exists)


def test_downgrade_preserves_preexisting_shared_schema_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    config = _config(service_dir)

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("AI_SIGNAL_DATABASE_URL", url)
        asyncio.run(_create_shared_object(url))

        command.upgrade(config, "head")
        command.downgrade(config, "base")

        assert asyncio.run(_shared_object_exists(url)) is True

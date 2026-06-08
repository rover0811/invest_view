# pyright: reportAttributeAccessIssue=false, reportMissingImports=false
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import TypedDict

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer


class _AgentMigrationState(TypedDict):
    schemas: set[str]
    tables: set[tuple[str, str]]
    indexes: set[str]
    has_parent_self_fk: bool
    status_default: str | None


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


async def _fetch_agent_state(url: str) -> _AgentMigrationState:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        schemas = set(
            await conn.scalars(
                text(
                    """SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name IN ('agent')"""
                )
            )
        )
        table_rows = await conn.execute(
            text(
                """SELECT table_schema, table_name FROM information_schema.tables
                WHERE table_schema = 'agent' AND table_type = 'BASE TABLE'"""
            )
        )
        indexes = set(
            await conn.scalars(
                text(
                    """SELECT indexname FROM pg_indexes
                    WHERE schemaname = 'agent'
                    AND indexname IN ('idx_sessions_user_active','idx_messages_session_parent')"""
                )
            )
        )
        has_parent_self_fk = bool(
            await conn.scalar(
                text(
                    """SELECT EXISTS (
                        SELECT 1
                        FROM pg_constraint c
                        JOIN pg_class child ON child.oid = c.conrelid
                        JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
                        JOIN pg_class parent ON parent.oid = c.confrelid
                        JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
                        WHERE c.contype = 'f'
                          AND child_ns.nspname = 'agent'
                          AND child.relname = 'chat_messages'
                          AND parent_ns.nspname = 'agent'
                          AND parent.relname = 'chat_messages'
                    )"""
                )
            )
        )
        status_default = await conn.scalar(
            text(
                """SELECT column_default FROM information_schema.columns
                WHERE table_schema = 'agent'
                  AND table_name = 'chat_messages'
                  AND column_name = 'status'"""
            )
        )
    await engine.dispose()
    return {
        "schemas": schemas,
        "tables": {(row[0], row[1]) for row in table_rows},
        "indexes": indexes,
        "has_parent_self_fk": has_parent_self_fk,
        "status_default": status_default,
    }


async def _assert_message_cascade(url: str) -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    root_message_id = uuid.uuid4()
    child_message_id = uuid.uuid4()

    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO alert_service.users (user_id, nickname) VALUES (:user_id, 'qa')"),
            {"user_id": user_id},
        )
        await conn.execute(
            text(
                """INSERT INTO agent.chat_sessions (session_id, user_id, ticker)
                VALUES (:session_id, :user_id, '005930')"""
            ),
            {"session_id": session_id, "user_id": user_id},
        )
        await conn.execute(
            text(
                """INSERT INTO agent.chat_messages (message_id, session_id, role, content)
                VALUES (:message_id, :session_id, 'user', 'hello')"""
            ),
            {"message_id": root_message_id, "session_id": session_id},
        )
        await conn.execute(
            text(
                """INSERT INTO agent.chat_messages
                (message_id, session_id, parent_id, role, content)
                VALUES (:message_id, :session_id, :parent_id, 'assistant', 'hi')"""
            ),
            {
                "message_id": child_message_id,
                "session_id": session_id,
                "parent_id": root_message_id,
            },
        )
        await conn.execute(
            text("DELETE FROM agent.chat_sessions WHERE session_id = :session_id"),
            {"session_id": session_id},
        )
        message_count = await conn.scalar(text("SELECT count(*) FROM agent.chat_messages"))
    await engine.dispose()

    assert message_count == 0


async def _fetch_agent_schemas(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        schemas = set(
            await conn.scalars(
                text(
                    """SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name = 'agent'"""
                )
            )
        )
    await engine.dispose()
    return schemas


@pytest.mark.qa
def test_agent_chat_migration_upgrade_and_downgrade(monkeypatch: pytest.MonkeyPatch):
    service_dir = Path(__file__).resolve().parents[1]
    cfg = _alembic_config(service_dir)
    monkeypatch.setenv("TESTCONTAINERS_RYUK_DISABLED", "true")

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("ALERT_SERVICE_DATABASE_URL", url)

        command.upgrade(cfg, "head")
        state = asyncio.run(_fetch_agent_state(url))

        assert state["schemas"] == {"agent"}
        assert state["tables"] == {("agent", "chat_sessions"), ("agent", "chat_messages")}
        assert state["has_parent_self_fk"] is True
        assert state["status_default"] is not None
        assert "'complete'" in state["status_default"]
        assert state["indexes"] == {"idx_sessions_user_active", "idx_messages_session_parent"}

        asyncio.run(_assert_message_cascade(url))

        command.downgrade(cfg, "base")
        assert asyncio.run(_fetch_agent_schemas(url)) == set()

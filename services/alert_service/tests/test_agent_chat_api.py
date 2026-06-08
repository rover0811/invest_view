# pyright: reportMissingImports=false
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.auth.jwt import JWTVerifier
from alert_service.db.models import Base
from alert_service.db.session import create_engine, create_session_factory


pytestmark = pytest.mark.qa

SECRET = "test-secret-32chars-min-for-hs256"


def _token(user_id: uuid.UUID) -> str:
    return jwt.encode({"sub": str(user_id)}, SECRET, algorithm="HS256")


def _make_container(engine, session_factory):
    container = MagicMock()
    container.config.allow_origins = []
    container.jwt_verifier = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
    container.engine = engine
    container.session_factory = session_factory
    return container


@pytest_asyncio.fixture
async def chat_env(postgres_container):
    url = (
        postgres_container.get_connection_url()
        .replace("postgresql+psycopg2", "postgresql+asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS agent, alert_service CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA alert_service")
        await conn.exec_driver_sql("CREATE SCHEMA agent")
        await conn.run_sync(Base.metadata.create_all)

    session_factory = create_session_factory(engine)
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO alert_service.users (user_id, nickname) "
                "VALUES (:user_a, 'user-a'), (:user_b, 'user-b')"
            ),
            {"user_a": user_a, "user_b": user_b},
        )
        await session.commit()

    app = create_app(_make_container(engine, session_factory))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory, user_a, user_b

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS agent, alert_service CASCADE")
    await engine.dispose()


async def _seed_message(
    session_factory,
    *,
    message_id: uuid.UUID,
    session_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    role: str,
    content: str,
    created_at: datetime,
) -> None:
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO agent.chat_messages "
                "(message_id, session_id, parent_id, role, content, created_at) "
                "VALUES (:mid, :sid, :pid, :role, :content, :created_at)"
            ),
            {
                "mid": message_id,
                "sid": session_id,
                "pid": parent_id,
                "role": role,
                "content": content,
                "created_at": created_at,
            },
        )
        await session.commit()


async def test_chat_crud_active_path_and_authz(chat_env):
    client, session_factory, user_a, user_b = chat_env
    headers_a = {"Authorization": f"Bearer {_token(user_a)}"}
    headers_b = {"Authorization": f"Bearer {_token(user_b)}"}

    created = await client.post(
        "/api/agent/sessions",
        headers=headers_a,
        json={"ticker": "005930"},
    )

    assert created.status_code == 201
    created_body = created.json()
    session_id = uuid.UUID(created_body["session_id"])
    assert created_body["ticker"] == "005930"
    assert created_body["created_at"]

    async with session_factory() as session:
        owner = await session.scalar(
            text("SELECT user_id FROM agent.chat_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )
    assert owner == user_a

    listed = await client.get("/api/agent/sessions", headers=headers_a)
    assert listed.status_code == 200
    assert [row["session_id"] for row in listed.json()] == [str(session_id)]

    root = uuid.uuid4()
    a1 = uuid.uuid4()
    a2 = uuid.uuid4()
    t0 = datetime(2026, 6, 1, 0, 0)
    await _seed_message(
        session_factory,
        message_id=root,
        session_id=session_id,
        parent_id=None,
        role="user",
        content="root",
        created_at=t0,
    )
    await _seed_message(
        session_factory,
        message_id=a1,
        session_id=session_id,
        parent_id=root,
        role="assistant",
        content="A1",
        created_at=t0 + timedelta(seconds=1),
    )
    await _seed_message(
        session_factory,
        message_id=a2,
        session_id=session_id,
        parent_id=root,
        role="assistant",
        content="A2",
        created_at=t0 + timedelta(seconds=2),
    )

    messages = await client.get(f"/api/agent/sessions/{session_id}/messages", headers=headers_a)
    assert messages.status_code == 200
    body = messages.json()
    assert [row["message_id"] for row in body] == [str(root), str(a2)]
    assert [row["content"] for row in body] == ["root", "A2"]
    assert str(a1) not in {row["message_id"] for row in body}

    assert (
        await client.get(f"/api/agent/sessions/{session_id}/messages", headers=headers_b)
    ).status_code == 404
    assert (await client.delete(f"/api/agent/sessions/{session_id}", headers=headers_b)).status_code == 404

    patched = await client.patch(
        f"/api/agent/sessions/{session_id}",
        headers=headers_a,
        json={"title": "Samsung chat"},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Samsung chat"
    assert patched.json()["is_archived"] is False

    deleted = await client.delete(f"/api/agent/sessions/{session_id}", headers=headers_a)
    assert deleted.status_code == 204

    listed_after_delete = await client.get("/api/agent/sessions", headers=headers_a)
    assert listed_after_delete.status_code == 200
    assert listed_after_delete.json() == []
    async with session_factory() as session:
        archived = await session.scalar(
            text("SELECT is_archived FROM agent.chat_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )
    assert archived is True

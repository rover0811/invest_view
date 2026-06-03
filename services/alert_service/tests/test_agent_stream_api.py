from __future__ import annotations

# pyright: reportMissingImports=false

import uuid
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.api.routes import agent_chat
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


async def _create_chat(client: httpx.AsyncClient, user_id: uuid.UUID) -> uuid.UUID:
    response = await client.post(
        "/api/agent/sessions",
        headers={"Authorization": f"Bearer {_token(user_id)}"},
        json={"ticker": "005930"},
    )
    assert response.status_code == 201
    return uuid.UUID(response.json()["session_id"])


async def _messages(session_factory, session_id: uuid.UUID) -> list[dict[str, object]]:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT message_id, parent_id, role, content, status, error "
                "FROM agent.chat_messages WHERE session_id = :sid"
            ),
            {"sid": session_id},
        )
    return [dict(row._mapping) for row in result.all()]


class FakeAgent:
    async def stream_async(self, text, invocation_state=None):
        assert text == "q"
        assert invocation_state is not None
        assert invocation_state["current_ticker"] == "005930"
        assert invocation_state["session_factory"] is not None
        yield {"data": "A"}
        yield {"data": "B"}


class ErrorAgent:
    async def stream_async(self, text, invocation_state=None):
        if text:
            raise RuntimeError("boom")
        yield {"data": "never"}


async def test_stream_success(chat_env, monkeypatch):
    client, session_factory, user_a, _user_b = chat_env
    session_id = await _create_chat(client, user_a)
    monkeypatch.setattr(agent_chat, "build_market_analyst_agent", lambda *args, **kwargs: FakeAgent())

    response = await client.post(
        f"/api/agent/sessions/{session_id}/stream",
        headers={"Authorization": f"Bearer {_token(user_a)}"},
        json={"text": "q"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: token\ndata: {"text": "A"}' in response.text
    assert 'event: token\ndata: {"text": "B"}' in response.text
    assert 'event: done\ndata: {' in response.text
    assert '"status": "complete"' in response.text

    rows = await _messages(session_factory, session_id)
    rows_by_role = {row["role"]: row for row in rows}
    assert rows_by_role["user"]["content"] == "q"
    assert rows_by_role["user"]["status"] == "complete"
    assert rows_by_role["assistant"]["content"] == "AB"
    assert rows_by_role["assistant"]["status"] == "complete"
    assert rows_by_role["assistant"]["parent_id"] == rows_by_role["user"]["message_id"]


async def test_stream_error(chat_env, monkeypatch):
    client, session_factory, user_a, _user_b = chat_env
    session_id = await _create_chat(client, user_a)
    monkeypatch.setattr(agent_chat, "build_market_analyst_agent", lambda *args, **kwargs: ErrorAgent())

    response = await client.post(
        f"/api/agent/sessions/{session_id}/stream",
        headers={"Authorization": f"Bearer {_token(user_a)}"},
        json={"text": "q"},
    )

    assert response.status_code == 200
    assert 'event: error\ndata: {"message": "boom"}' in response.text
    assert "event: done" not in response.text

    rows = await _messages(session_factory, session_id)
    rows_by_role = {row["role"]: row for row in rows}
    assert rows_by_role["assistant"]["content"] == ""
    assert rows_by_role["assistant"]["status"] == "error"
    assert rows_by_role["assistant"]["error"] == {"message": "boom"}


async def test_stream_interrupt(chat_env, monkeypatch):
    client, session_factory, user_a, _user_b = chat_env
    session_id = await _create_chat(client, user_a)
    monkeypatch.setattr(agent_chat, "build_market_analyst_agent", lambda *args, **kwargs: FakeAgent())
    disconnect_checks = 0

    async def fake_is_disconnected(self):
        nonlocal disconnect_checks
        disconnect_checks += 1
        return disconnect_checks > 1

    monkeypatch.setattr("starlette.requests.Request.is_disconnected", fake_is_disconnected)

    response = await client.post(
        f"/api/agent/sessions/{session_id}/stream",
        headers={"Authorization": f"Bearer {_token(user_a)}"},
        json={"text": "q"},
    )

    assert response.status_code == 200
    assert 'event: token\ndata: {"text": "A"}' in response.text
    assert 'event: token\ndata: {"text": "B"}' not in response.text
    assert '"status": "interrupted"' in response.text

    rows = await _messages(session_factory, session_id)
    assistant = {row["role"]: row for row in rows}["assistant"]
    assert assistant["content"] == "A"
    assert assistant["status"] == "interrupted"


async def test_stream_authz(chat_env):
    client, session_factory, user_a, user_b = chat_env
    session_id = await _create_chat(client, user_a)

    response = await client.post(
        f"/api/agent/sessions/{session_id}/stream",
        headers={"Authorization": f"Bearer {_token(user_b)}"},
        json={"text": "q"},
    )

    assert response.status_code == 404
    assert await _messages(session_factory, session_id) == []

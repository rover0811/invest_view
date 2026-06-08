from __future__ import annotations

# pyright: reportMissingImports=false

import json
import uuid
from unittest.mock import MagicMock

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.agent import title as title_module
from alert_service.api.app import create_app
from alert_service.api.routes import agent_chat
from alert_service.auth.jwt import JWTVerifier
from alert_service.db.models import Base
from alert_service.db.session import create_engine, create_session_factory


pytestmark = pytest.mark.qa

SECRET = "test-secret-32chars-min-for-hs256"

CHART_SPEC = {
    "chart_type": "line",
    "title": "005930 재무 추이",
    "x_label": "기간",
    "y_label": "천원",
    "unit": "천원",
    "series": [{"name": "매출액(수익)", "points": [{"x": "2024-12", "y": 57000000.0}]}],
}
END_CHART_SPEC = {
    "chart_type": "bar",
    "title": "005930 영업이익 추이",
    "x_label": "기간",
    "y_label": "천원",
    "unit": "천원",
    "series": [{"name": "영업이익", "points": [{"x": "2024-12", "y": 12000000.0}]}],
}


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
                "FROM agent.chat_messages WHERE session_id = :sid ORDER BY created_at ASC"
            ),
            {"sid": session_id},
        )
    return [dict(row._mapping) for row in result.all()]


async def _session_title(session_factory, session_id: uuid.UUID) -> str | None:
    async with session_factory() as session:
        return await session.scalar(
            text("SELECT title FROM agent.chat_sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )


def _done_message_id(response_text: str) -> uuid.UUID:
    for chunk in response_text.split("\n\n"):
        if chunk.startswith("event: done\ndata: "):
            return uuid.UUID(json.loads(chunk.removeprefix("event: done\ndata: "))["message_id"])
    raise AssertionError("done event not found")


class FakeAgent:
    async def stream_async(self, text, invocation_state=None):
        assert text == "q"
        assert invocation_state is not None
        assert invocation_state["current_ticker"] == "005930"
        assert invocation_state["session_factory"] is not None
        assert invocation_state["chart_sink"] is not None
        invocation_state["chart_sink"].put_nowait(CHART_SPEC)
        yield {"data": "X"}
        yield {"data": "Y"}
        invocation_state["chart_sink"].put_nowait(END_CHART_SPEC)


async def test_regenerate_creates_sibling_and_active_path_uses_new_leaf(chat_env, monkeypatch):
    client, session_factory, user_a, _user_b = chat_env
    headers = {"Authorization": f"Bearer {_token(user_a)}"}
    session_id = await _create_chat(client, user_a)
    monkeypatch.setattr(agent_chat, "build_market_analyst_agent", lambda *args, **kwargs: FakeAgent())

    first = await client.post(
        f"/api/agent/sessions/{session_id}/stream",
        headers=headers,
        json={"text": "q"},
    )
    assert first.status_code == 200
    a1_id = _done_message_id(first.text)
    rows_after_first = await _messages(session_factory, session_id)
    user_message = next(row for row in rows_after_first if row["role"] == "user")

    regenerated = await client.post(
        f"/api/agent/sessions/{session_id}/messages/{a1_id}/regenerate",
        headers=headers,
    )

    assert regenerated.status_code == 200
    chunks = regenerated.text.split("\n\n")
    chart_chunks = [chunk for chunk in chunks if chunk.startswith("event: chart\ndata: ")]
    assert [json.loads(chunk.removeprefix("event: chart\ndata: ")) for chunk in chart_chunks] == [
        {"spec": CHART_SPEC},
        {"spec": END_CHART_SPEC},
    ]
    assert chunks.index(f"event: chart\ndata: {json.dumps({'spec': CHART_SPEC}, ensure_ascii=False)}") < chunks.index(
        'event: token\ndata: {"text": "X"}'
    )
    assert chunks.index('event: token\ndata: {"text": "Y"}') < chunks.index(
        f"event: chart\ndata: {json.dumps({'spec': END_CHART_SPEC}, ensure_ascii=False)}"
    )
    assert chunks.index(f"event: chart\ndata: {json.dumps({'spec': END_CHART_SPEC}, ensure_ascii=False)}") < next(
        index for index, chunk in enumerate(chunks) if chunk.startswith("event: done\ndata: ")
    )
    assert 'event: token\ndata: {"text": "X"}' in regenerated.text
    assert 'event: token\ndata: {"text": "Y"}' in regenerated.text
    a2_id = _done_message_id(regenerated.text)
    assert a2_id != a1_id

    rows = await _messages(session_factory, session_id)
    assistants = [row for row in rows if row["role"] == "assistant"]
    assert {row["message_id"] for row in assistants} == {a1_id, a2_id}
    assert [row["parent_id"] for row in assistants] == [user_message["message_id"], user_message["message_id"]]
    assert all(row["status"] == "complete" for row in assistants)

    active = await client.get(f"/api/agent/sessions/{session_id}/messages", headers=headers)
    assert active.status_code == 200
    assert [row["message_id"] for row in active.json()] == [str(user_message["message_id"]), str(a2_id)]
    assert str(a1_id) not in {row["message_id"] for row in active.json()}


async def test_regenerate_authz_returns_404(chat_env, monkeypatch):
    client, _session_factory, user_a, user_b = chat_env
    session_id = await _create_chat(client, user_a)
    monkeypatch.setattr(agent_chat, "build_market_analyst_agent", lambda *args, **kwargs: FakeAgent())

    first = await client.post(
        f"/api/agent/sessions/{session_id}/stream",
        headers={"Authorization": f"Bearer {_token(user_a)}"},
        json={"text": "q"},
    )
    assert first.status_code == 200
    a1_id = _done_message_id(first.text)

    response = await client.post(
        f"/api/agent/sessions/{session_id}/messages/{a1_id}/regenerate",
        headers={"Authorization": f"Bearer {_token(user_b)}"},
    )

    assert response.status_code == 404


async def test_auto_title_success_and_failure_safe(chat_env, monkeypatch):
    client, session_factory, user_a, _user_b = chat_env
    headers = {"Authorization": f"Bearer {_token(user_a)}"}
    monkeypatch.setattr(agent_chat, "build_market_analyst_agent", lambda *args, **kwargs: FakeAgent())

    titled_session = await _create_chat(client, user_a)
    response = await client.post(
        f"/api/agent/sessions/{titled_session}/stream",
        headers=headers,
        json={"text": "q"},
    )
    assert response.status_code == 200
    assert 'event: done\ndata: {' in response.text
    assert await _session_title(session_factory, titled_session) == "q"

    async def raising_generate_title(config, first_question, first_answer):
        raise RuntimeError("title boom")

    monkeypatch.setattr(title_module, "generate_title", raising_generate_title)
    untitled_session = await _create_chat(client, user_a)
    response = await client.post(
        f"/api/agent/sessions/{untitled_session}/stream",
        headers=headers,
        json={"text": "q"},
    )

    assert response.status_code == 200
    assert 'event: done\ndata: {' in response.text
    assert await _session_title(session_factory, untitled_session) is None

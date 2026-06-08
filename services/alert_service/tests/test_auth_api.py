from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from alert_service.api.app import create_app
from alert_service.auth.jwt import JWTVerifier
from alert_service.db.models import User
from alert_service.repository.users import UserRepository


pytestmark = pytest.mark.qa

SECRET = "test-secret-32chars-min-for-hs256"
ALGORITHM = "HS256"
USER_ID_CLAIM = "sub"


def _make_container(engine, session_factory):
    container = MagicMock()
    container.config.allow_origins = []
    container.config.jwt_secret = SECRET
    container.config.jwt_algorithm = ALGORITHM
    container.config.jwt_user_id_claim = USER_ID_CLAIM
    container.engine = engine
    container.session_factory = session_factory
    container.jwt_verifier = JWTVerifier(
        secret=SECRET,
        algorithm=ALGORITHM,
        user_id_claim=USER_ID_CLAIM,
    )
    container.user_repo = UserRepository(session_factory)
    container.watchlist_repo = MagicMock()
    container.watchlist_repo.list_for_user = AsyncMock(return_value=[])
    container.notification_repo = MagicMock()
    container.connection_registry = MagicMock()
    container.alert_consumer = MagicMock()
    return container


@pytest_asyncio.fixture
async def auth_env(db_engine, db_session_factory):
    container = _make_container(db_engine, db_session_factory)
    app = create_app(container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db_session_factory, container


async def test_login_returns_token_and_user_id(auth_env):
    client, _sf, _container = auth_env

    resp = await client.post("/api/auth/login", json={"nickname": "alice"})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"token", "user_id"}
    assert isinstance(body["token"], str)
    assert isinstance(body["user_id"], str)
    uuid.UUID(body["user_id"])
    claims = jwt.decode(body["token"], SECRET, algorithms=[ALGORITHM])
    assert claims[USER_ID_CLAIM] == body["user_id"]


async def test_same_nickname_is_idempotent(auth_env):
    client, sf, _container = auth_env

    first = await client.post("/api/auth/login", json={"nickname": "bob"})
    second = await client.post("/api/auth/login", json={"nickname": "bob"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]
    async with sf() as session:
        count = await session.scalar(
            select(func.count()).select_from(User).where(User.nickname == "bob")
        )
    assert count == 1


async def test_different_nickname_gets_different_user_id(auth_env):
    client, _sf, _container = auth_env

    alice = await client.post("/api/auth/login", json={"nickname": "alice"})
    charlie = await client.post("/api/auth/login", json={"nickname": "charlie"})

    assert alice.status_code == 200
    assert charlie.status_code == 200
    assert alice.json()["user_id"] != charlie.json()["user_id"]


@pytest.mark.parametrize("nickname", ["", "   "])
async def test_empty_or_whitespace_nickname_returns_422(auth_env, nickname):
    client, _sf, _container = auth_env

    resp = await client.post("/api/auth/login", json={"nickname": nickname})

    assert resp.status_code == 422


async def test_nickname_exceeding_max_length_returns_422(auth_env):
    client, _sf, _container = auth_env

    resp = await client.post("/api/auth/login", json={"nickname": "a" * 65})

    assert resp.status_code == 422


async def test_issued_token_authenticates_guarded_endpoint(auth_env):
    client, _sf, container = auth_env
    login_resp = await client.post("/api/auth/login", json={"nickname": "dave"})
    token = login_resp.json()["token"]

    resp = await client.get("/api/watchlist", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code != 401
    assert resp.status_code == 200
    container.watchlist_repo.list_for_user.assert_awaited_once()

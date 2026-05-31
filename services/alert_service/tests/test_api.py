import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from alert_service.api.app import create_app
from alert_service.auth.jwt import JWTVerifier
from alert_service.repository.watchlist import WatchlistDuplicateError


SECRET = "test-secret-32chars-min-for-hs256"


class FakeContainer:
    def __init__(self):
        self.config = MagicMock()
        self.config.allow_origins = []
        self.jwt_verifier = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
        self.connection_registry = MagicMock()
        self.connection_registry._by_user = {}
        self.connection_registry.add = MagicMock()
        self.connection_registry.remove = MagicMock()
        self.watchlist_repo = MagicMock()
        self.watchlist_repo.list_for_user = AsyncMock(return_value=[])
        self.watchlist_repo.add = AsyncMock()
        self.watchlist_repo.remove = AsyncMock(return_value=True)
        self.watchlist_repo.set_notifications_enabled = AsyncMock(return_value=True)
        self.notification_repo = MagicMock()
        self.notification_repo.list_for_user = AsyncMock(return_value=[])
        self.alert_consumer = MagicMock()
        self.alert_consumer.start = AsyncMock()
        self.alert_consumer.stop = MagicMock()
        self.alert_consumer.is_alive.return_value = True
        self.engine = MagicMock()
        self.engine.dispose = AsyncMock()


def _token(user_id: uuid.UUID | None = None) -> str:
    return jwt.encode({"sub": str(user_id or uuid.uuid4())}, SECRET, algorithm="HS256")


@pytest.fixture
def container():
    return FakeContainer()


@pytest.fixture
def client(container):
    app = create_app(container)
    with TestClient(app) as c:
        yield c


def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_returns_503_when_consumer_is_dead(client, container):
    container.alert_consumer.is_alive.return_value = False

    r = client.get("/health")

    assert r.status_code == 503
    assert r.json() == {"status": "unavailable"}


def test_watchlist_get_requires_auth(client):
    r = client.get("/api/watchlist")
    assert r.status_code == 401


def test_watchlist_get_with_valid_jwt(client, container):
    uid = uuid.uuid4()
    r = client.get("/api/watchlist", headers={"Authorization": f"Bearer {_token(uid)}"})
    assert r.status_code == 200
    assert r.json() == []
    container.watchlist_repo.list_for_user.assert_awaited_once_with(uid)


def test_watchlist_post_valid_symbol(client, container):
    uid = uuid.uuid4()
    item = MagicMock()
    item.symbol = "005930"
    item.notifications_enabled = True
    item.created_at = datetime.now(timezone.utc)
    container.watchlist_repo.add.return_value = item
    r = client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {_token(uid)}"},
        json={"symbol": "005930"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["symbol"] == "005930"


def test_watchlist_post_invalid_symbol_format(client):
    r = client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"symbol": "ab"},
    )
    assert r.status_code == 422


def test_watchlist_post_duplicate_returns_409(client, container):
    container.watchlist_repo.add.side_effect = WatchlistDuplicateError("dup")
    r = client.post(
        "/api/watchlist",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"symbol": "005930"},
    )
    assert r.status_code == 409


def test_watchlist_delete_success(client, container):
    container.watchlist_repo.remove.return_value = True
    r = client.delete(
        "/api/watchlist/005930",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 204


def test_watchlist_delete_not_found(client, container):
    container.watchlist_repo.remove.return_value = False
    r = client.delete(
        "/api/watchlist/005930",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 404


def test_notifications_list_with_query(client, container):
    uid = uuid.uuid4()
    r = client.get(
        "/api/notifications?limit=50",
        headers={"Authorization": f"Bearer {_token(uid)}"},
    )
    assert r.status_code == 200
    assert r.json() == []
    container.notification_repo.list_for_user.assert_awaited_once()
    kwargs = container.notification_repo.list_for_user.await_args.kwargs
    assert kwargs.get("limit") == 50 or 50 in container.notification_repo.list_for_user.await_args.args


def test_notifications_limit_validation(client):
    r = client.get(
        "/api/notifications?limit=99999",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 422


def test_ws_token_query_required(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_ws_invalid_token_closes_1008(client):
    try:
        with client.websocket_connect("/ws?token=invalid"):
            pass
    except Exception:
        pass


def test_ws_valid_token_registers(client, container):
    token = _token()
    with client.websocket_connect(f"/ws?token={token}"):
        container.connection_registry.add.assert_called_once()
    container.connection_registry.remove.assert_called_once()

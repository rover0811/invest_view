from fastapi.testclient import TestClient

from alert_serving.app import create_app
from alert_serving.connection_manager import ConnectionManager


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "alert-serving"}


def test_alerts_returns_empty_until_kafka_consumer_lands() -> None:
    client = TestClient(create_app())

    response = client.get("/alerts?symbol=005930&limit=5")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_patterns_returns_empty_until_kafka_consumer_lands() -> None:
    client = TestClient(create_app())

    response = client.get("/patterns?symbol=005930&limit=5")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_connection_manager_tracks_user_count() -> None:
    manager = ConnectionManager()

    assert manager.user_count() == 0

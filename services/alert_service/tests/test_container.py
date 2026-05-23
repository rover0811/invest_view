import pytest
from alert_service.config import AlertServiceConfig
from alert_service.container import Container


@pytest.fixture
def cfg(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("ALERT_SERVICE_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALERT_SERVICE_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/invest_view")
    monkeypatch.setenv("ALERT_SERVICE_KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("ALERT_SERVICE_SCHEMA_REGISTRY_URL", "http://localhost:8081")
    monkeypatch.setenv("ALERT_SERVICE_JWT_SECRET", "test-secret-32chars-min-for-hs256")
    # Point to the real schema file in the workspace root
    schema_path = __import__("pathlib").Path(__file__).resolve().parents[3] / "schemas" / "stock-alerts.avsc"
    monkeypatch.setenv("ALERT_SERVICE_AVRO_SCHEMA_PATH", str(schema_path))
    return AlertServiceConfig()


def test_container_builds_object_graph(cfg):
    container = Container(cfg)
    assert container.config is cfg
    assert container.engine is not None
    assert container.session_factory is not None
    assert container.jwt_verifier is not None
    assert container.connection_registry is not None
    assert container.user_repo is not None
    assert container.watchlist_repo is not None
    assert container.alert_event_repo is not None
    assert container.notification_repo is not None
    assert container.alert_pusher is not None
    assert container.alert_consumer is not None


def test_alert_consumer_uses_pusher_as_handler(cfg):
    container = Container(cfg)
    assert container.alert_consumer._on_message == container.alert_pusher.handle

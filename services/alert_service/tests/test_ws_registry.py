import pytest
from alert_service.ws.registry import ConnectionRegistry


class FakeWS:
    def __init__(self) -> None:
        self.received: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.received.append(payload)


class FailingWS:
    async def send_json(self, payload: dict) -> None:
        raise RuntimeError("disconnected")


def test_add_get_remove_basic():
    r = ConnectionRegistry()
    w = FakeWS()
    r.add("u1", w)
    assert r.is_connected("u1")
    assert r.get_connections("u1") == {w}
    r.remove("u1", w)
    assert not r.is_connected("u1")
    assert r.get_connections("u1") == set()


def test_multiple_connections_same_user():
    r = ConnectionRegistry()
    w1, w2 = FakeWS(), FakeWS()
    r.add("u1", w1)
    r.add("u1", w2)
    assert r.get_connections("u1") == {w1, w2}
    assert r.is_connected("u1")
    r.remove("u1", w1)
    assert r.get_connections("u1") == {w2}


def test_remove_unknown_user_noop():
    r = ConnectionRegistry()
    r.remove("nonexistent", FakeWS())  # must not raise


def test_remove_unknown_ws_noop():
    r = ConnectionRegistry()
    w1, w2 = FakeWS(), FakeWS()
    r.add("u1", w1)
    r.remove("u1", w2)  # w2 not in set; must not raise
    assert r.get_connections("u1") == {w1}


def test_is_connected_false_when_no_user():
    r = ConnectionRegistry()
    assert not r.is_connected("ghost")


@pytest.mark.asyncio
async def test_send_to_user_broadcasts_to_all_connections():
    r = ConnectionRegistry()
    w1, w2 = FakeWS(), FakeWS()
    r.add("u1", w1)
    r.add("u1", w2)
    sent, failed = await r.send_to_user("u1", {"type": "alert"})
    assert sent == 2
    assert failed == 0
    assert w1.received == [{"type": "alert"}]
    assert w2.received == [{"type": "alert"}]


@pytest.mark.asyncio
async def test_send_to_user_removes_failed_connections():
    r = ConnectionRegistry()
    good = FakeWS()
    bad = FailingWS()
    r.add("u1", good)
    r.add("u1", bad)
    sent, failed = await r.send_to_user("u1", {"hi": 1})
    assert sent == 1
    assert failed == 1
    assert good.received == [{"hi": 1}]
    # bad ws auto-removed
    assert r.get_connections("u1") == {good}


@pytest.mark.asyncio
async def test_send_to_user_unknown_returns_zero():
    r = ConnectionRegistry()
    sent, failed = await r.send_to_user("ghost", {"x": 1})
    assert sent == 0
    assert failed == 0


def test_total_counts():
    r = ConnectionRegistry()
    r.add("u1", FakeWS())
    r.add("u1", FakeWS())
    r.add("u2", FakeWS())
    assert r.total_users() == 2
    assert r.total_connections() == 3

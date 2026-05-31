"""Repository QA tests against real testcontainers Postgres.

Marked with @pytest.mark.qa. Run with: pytest -m qa
Skip in fast unit runs with: pytest -m "not qa"
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from alert_service.db.models import User
from alert_service.repository.users import UserRepository
from alert_service.repository.watchlist import (
    WatchlistDuplicateError,
    WatchlistRepository,
)
from alert_service.repository.alert_events import AlertEventRepository
from alert_service.repository.notifications import NotificationRepository


pytestmark = pytest.mark.qa


@pytest.fixture
async def seeded_user(db_session_factory) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with db_session_factory() as session:
        session.add(User(user_id=user_id, nickname="tester"))
        await session.commit()
    return user_id


def _alert_payload(
    symbol: str = "005930", alert_event_id: uuid.UUID | None = None
) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return {
        "alert_event_id": alert_event_id or uuid.uuid4(),
        "symbol": symbol,
        "market": "KRX",
        "alert_type": "PRICE_ALERT",
        "severity": "WARNING",
        "observation_start_at": now,
        "observation_end_at": now,
        "triggered_at": now,
        "trigger_values": {"current_price": "72000"},
        "source_tick_event_id": None,
        "rule_name": "rule_1",
    }


async def test_user_exists_and_get(db_session_factory, seeded_user):
    repo = UserRepository(db_session_factory)
    assert await repo.exists(seeded_user) is True
    user = await repo.get(seeded_user)
    assert user is not None and user.nickname == "tester"
    assert await repo.exists(uuid.uuid4()) is False


async def test_watchlist_add_list_remove(db_session_factory, seeded_user):
    repo = WatchlistRepository(db_session_factory)
    item = await repo.add(seeded_user, "005930")
    assert item.symbol == "005930"
    items = await repo.list_for_user(seeded_user)
    assert len(items) == 1
    removed = await repo.remove(seeded_user, "005930")
    assert removed is True
    assert await repo.list_for_user(seeded_user) == []


async def test_watchlist_duplicate_raises(db_session_factory, seeded_user):
    repo = WatchlistRepository(db_session_factory)
    await repo.add(seeded_user, "005930")
    with pytest.raises(WatchlistDuplicateError):
        await repo.add(seeded_user, "005930")


async def test_watchlist_toggle_notifications(db_session_factory, seeded_user):
    repo = WatchlistRepository(db_session_factory)
    await repo.add(seeded_user, "005930")
    assert await repo.set_notifications_enabled(seeded_user, "005930", False) is True
    items = await repo.list_for_user(seeded_user)
    assert items[0].notifications_enabled is False


async def test_find_users_for_symbol_filters_disabled(db_session_factory):
    repo = WatchlistRepository(db_session_factory)
    ua, ub, uc = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with db_session_factory() as session:
        session.add_all(
            [
                User(user_id=ua, nickname="A"),
                User(user_id=ub, nickname="B"),
                User(user_id=uc, nickname="C"),
            ]
        )
        await session.commit()
    await repo.add(ua, "005930")
    await repo.add(ub, "005930")
    await repo.set_notifications_enabled(ub, "005930", False)
    await repo.add(uc, "000660")
    found = await repo.find_users_for_symbol("005930")
    assert found == [ua]


async def test_alert_event_upsert_idempotent(db_session_factory):
    repo = AlertEventRepository(db_session_factory)
    aid = uuid.uuid4()
    payload = _alert_payload(alert_event_id=aid)
    assert await repo.upsert(payload) is True
    assert await repo.upsert(payload) is False
    event = await repo.get(aid)
    assert event is not None and event.symbol == "005930"


async def test_notifications_bulk_create_and_mark(db_session_factory, seeded_user):
    alert_repo = AlertEventRepository(db_session_factory)
    notif_repo = NotificationRepository(db_session_factory)
    aid = uuid.uuid4()
    await alert_repo.upsert(_alert_payload(alert_event_id=aid))
    created = await notif_repo.bulk_create_pending([seeded_user], aid, "005930")
    assert len(created) == 1
    assert created[0][0] == seeded_user
    again = await notif_repo.bulk_create_pending([seeded_user], aid, "005930")
    assert again == created
    nid = created[0][1]
    await notif_repo.mark_sent(nid, datetime.now(timezone.utc).replace(tzinfo=None))
    items = await notif_repo.list_for_user(seeded_user, since=None, limit=10)
    assert len(items) == 1 and items[0].delivery_status == "SENT"


async def test_notifications_bulk_create_pending_replay_returns_only_pending_pairs(
    db_session_factory,
):
    alert_repo = AlertEventRepository(db_session_factory)
    notif_repo = NotificationRepository(db_session_factory)
    aid = uuid.uuid4()
    user_pending, user_sent, user_failed = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with db_session_factory() as session:
        session.add_all(
            [
                User(user_id=user_pending, nickname="pending"),
                User(user_id=user_sent, nickname="sent"),
                User(user_id=user_failed, nickname="failed"),
            ]
        )
        await session.commit()
    await alert_repo.upsert(_alert_payload(alert_event_id=aid))

    created = await notif_repo.bulk_create_pending(
        [user_pending, user_sent, user_failed], aid, "005930"
    )
    assert {uid for uid, _nid in created} == {user_pending, user_sent, user_failed}
    by_user = dict(created)

    await notif_repo.mark_sent(
        by_user[user_sent], datetime.now(timezone.utc).replace(tzinfo=None)
    )
    await notif_repo.mark_failed(
        by_user[user_failed],
        datetime.now(timezone.utc).replace(tzinfo=None),
        "no_connection",
    )

    replay = await notif_repo.bulk_create_pending(
        [user_pending, user_sent, user_failed], aid, "005930"
    )

    assert replay == [(user_pending, by_user[user_pending])]


async def test_notifications_mark_failed(db_session_factory, seeded_user):
    alert_repo = AlertEventRepository(db_session_factory)
    notif_repo = NotificationRepository(db_session_factory)
    aid = uuid.uuid4()
    await alert_repo.upsert(_alert_payload(alert_event_id=aid))
    nids = await notif_repo.bulk_create_pending([seeded_user], aid, "005930")
    await notif_repo.mark_failed(
        nids[0][1], datetime.now(timezone.utc).replace(tzinfo=None), "no_connection"
    )
    items = await notif_repo.list_for_user(seeded_user, since=None, limit=10)
    assert items[0].delivery_status == "FAILED"
    assert items[0].failure_reason == "no_connection"


async def test_notifications_pagination_with_since(db_session_factory, seeded_user):
    alert_repo = AlertEventRepository(db_session_factory)
    notif_repo = NotificationRepository(db_session_factory)
    for _ in range(5):
        aid = uuid.uuid4()
        await alert_repo.upsert(_alert_payload(alert_event_id=aid))
        await notif_repo.bulk_create_pending([seeded_user], aid, "005930")
    all_items = await notif_repo.list_for_user(seeded_user, since=None, limit=100)
    assert len(all_items) == 5
    third_newest_created = all_items[2].created_at
    newer = await notif_repo.list_for_user(
        seeded_user, since=third_newest_created, limit=100
    )
    assert len(newer) == 2

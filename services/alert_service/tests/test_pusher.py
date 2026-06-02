import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from alert_service.ws.pusher import AlertPusher


def _event(symbol="005930", alert_event_id=None):
    now = datetime(2026, 5, 22, 0, 5, 0, tzinfo=timezone.utc)
    return {
        "alert_event_id": str(alert_event_id or uuid.uuid4()),
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


@pytest.fixture
def deps():
    alert_repo = MagicMock()
    alert_repo.upsert = AsyncMock(return_value=True)
    watchlist_repo = MagicMock()
    watchlist_repo.find_users_for_symbol = AsyncMock(return_value=[])
    notif_repo = MagicMock()
    notif_repo.bulk_create_pending = AsyncMock(return_value=[])
    notif_repo.mark_sent = AsyncMock()
    notif_repo.mark_failed = AsyncMock()
    registry = MagicMock()
    registry.is_connected = MagicMock(return_value=False)
    registry.send_to_user = AsyncMock(return_value=(0, 0))
    return alert_repo, watchlist_repo, notif_repo, registry


async def test_duplicate_alert_resumes_fanout(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    alert_repo.upsert.return_value = False
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    await pusher.handle(_event())
    watchlist_repo.find_users_for_symbol.assert_called_once()
    notif_repo.bulk_create_pending.assert_called_once()
    registry.send_to_user.assert_awaited_once()
    notif_repo.mark_sent.assert_awaited_once_with(nid, notif_repo.mark_sent.call_args.args[1])


async def test_no_watchers_skips_notifications(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    watchlist_repo.find_users_for_symbol.return_value = []
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    await pusher.handle(_event())
    notif_repo.bulk_create_pending.assert_not_called()


async def test_nil_alert_event_id_raises_sentinel_before_upsert(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)

    with pytest.raises(RuntimeError, match="SENTINEL_FATAL: nil alert_event_id"):
        await pusher.handle(_event(alert_event_id=uuid.UUID(int=0)))

    alert_repo.upsert.assert_not_awaited()
    watchlist_repo.find_users_for_symbol.assert_not_awaited()
    notif_repo.bulk_create_pending.assert_not_awaited()
    registry.send_to_user.assert_not_called()


async def test_connected_user_marked_sent(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    await pusher.handle(_event())
    registry.send_to_user.assert_called_once()
    notif_repo.mark_sent.assert_called_once()
    notif_repo.mark_failed.assert_not_called()


async def test_disconnected_user_marked_failed_no_connection(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = False
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    await pusher.handle(_event())
    notif_repo.mark_failed.assert_called_once()
    call = notif_repo.mark_failed.call_args
    all_vals = tuple(call.args) + tuple(call.kwargs.values())
    assert "no_connection" in all_vals


async def test_send_failure_marked_send_error(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (0, 1)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    await pusher.handle(_event())
    notif_repo.mark_failed.assert_called_once()
    call = notif_repo.mark_failed.call_args
    all_vals = tuple(call.args) + tuple(call.kwargs.values())
    assert "send_error" in all_vals


async def test_payload_contains_expected_fields(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    aid = uuid.uuid4()
    await pusher.handle(_event(alert_event_id=aid))
    user_id_arg, payload = registry.send_to_user.call_args.args
    assert user_id_arg == str(uid)
    assert payload["type"] == "alert"
    assert payload["alert_event_id"] == str(aid)
    assert payload["notification_id"] == str(nid)
    assert payload["symbol"] == "005930"
    assert payload["market"] == "KRX"
    assert payload["triggered_at"].endswith("Z")


async def test_bulk_create_replay_returns_subset(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid1, uid2 = uuid.uuid4(), uuid.uuid4()
    nid2 = uuid.uuid4()
    watchlist_repo.find_users_for_symbol.return_value = [uid1, uid2]
    notif_repo.bulk_create_pending.return_value = [(uid2, nid2)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    await pusher.handle(_event())
    user_id_arg, payload = registry.send_to_user.call_args.args
    assert user_id_arg == str(uid2)
    assert payload["notification_id"] == str(nid2)
    notif_repo.mark_sent.assert_called_once()


async def test_multi_user_uses_repository_user_notification_mapping(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid_a, uid_b = uuid.uuid4(), uuid.uuid4()
    nid_a, nid_b = uuid.uuid4(), uuid.uuid4()
    watchlist_repo.find_users_for_symbol.return_value = [uid_a, uid_b]
    notif_repo.bulk_create_pending.return_value = [(uid_a, nid_a), (uid_b, nid_b)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)

    await pusher.handle(_event())

    sent_payloads = {
        call.args[0]: call.args[1]["notification_id"]
        for call in registry.send_to_user.call_args_list
    }
    assert sent_payloads == {str(uid_a): str(nid_a), str(uid_b): str(nid_b)}


async def test_retry_duplicate_alert_still_fans_out_pending_pairs(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    alert_repo.upsert.side_effect = [True, False]
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    pusher = AlertPusher(alert_repo, watchlist_repo, notif_repo, registry)
    event = _event()

    await pusher.handle(event)
    await pusher.handle(event)

    assert registry.send_to_user.await_count == 2
    assert notif_repo.mark_sent.await_count == 2


async def test_fanout_fail_after_alert_seam_then_retry_resumes(deps):
    alert_repo, watchlist_repo, notif_repo, registry = deps
    uid = uuid.uuid4()
    nid = uuid.uuid4()
    event = _event()
    pusher = AlertPusher(
        alert_repo,
        watchlist_repo,
        notif_repo,
        registry,
        fanout_fail_after_alert=True,
    )

    with pytest.raises(RuntimeError, match="FANOUT_FAIL_AFTER_ALERT seam"):
        await pusher.handle(event)

    alert_repo.upsert.assert_awaited_once()
    notif_repo.bulk_create_pending.assert_not_called()

    alert_repo.upsert.reset_mock()
    alert_repo.upsert.return_value = False
    watchlist_repo.find_users_for_symbol.return_value = [uid]
    notif_repo.bulk_create_pending.return_value = [(uid, nid)]
    registry.is_connected.return_value = True
    registry.send_to_user.return_value = (1, 0)
    retry_pusher = AlertPusher(
        alert_repo,
        watchlist_repo,
        notif_repo,
        registry,
        fanout_fail_after_alert=False,
    )

    await retry_pusher.handle(event)

    notif_repo.bulk_create_pending.assert_awaited_once()
    registry.send_to_user.assert_awaited_once()

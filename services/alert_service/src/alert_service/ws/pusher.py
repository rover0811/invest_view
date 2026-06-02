"""AlertPusher: end-to-end handling of a single stock-alert event.

Flow:
  1. Upsert alert_events (idempotent on alert_event_id).
  2. If alert is a duplicate, resume notification fanout.
  3. Find watchlist users for this symbol with notifications enabled.
  4. Bulk-create PENDING notifications.
  5. For each notification:
     - If user is connected via WebSocket -> send_json + mark_sent
     - Otherwise -> mark_failed(reason='no_connection')
     - send failure -> mark_failed(reason='send_error')

DB errors propagate so Kafka consumer skips offset commit and retries.
Registry send errors are swallowed (notification marked FAILED, but pusher
returns normally so the consumer commits and moves on).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from alert_service.repository.alert_events import AlertEventRepository
from alert_service.repository.watchlist import WatchlistRepository
from alert_service.repository.notifications import NotificationRepository
from alert_service.ws.registry import ConnectionRegistry


logger = logging.getLogger(__name__)

_SENTINEL_ALERT_EVENT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _strip_tz(dt: Any) -> Any:
    if isinstance(dt, datetime) and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _isoformat(dt: Any) -> Any:
    if isinstance(dt, datetime):
        aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return dt


class AlertPusher:
    def __init__(
        self,
        alert_repo: AlertEventRepository,
        watchlist_repo: WatchlistRepository,
        notification_repo: NotificationRepository,
        registry: ConnectionRegistry,
        fanout_fail_after_alert: bool = False,
    ) -> None:
        self._alerts = alert_repo
        self._watchlist = watchlist_repo
        self._notifications = notification_repo
        self._registry = registry
        self._fanout_fail_after_alert = fanout_fail_after_alert

    async def handle(self, event: dict[str, Any]) -> None:
        alert_event_id = self._coerce_uuid(event["alert_event_id"])
        # Decision D: reserved nil UUID is an always-on fatal replay seam.
        if alert_event_id == _SENTINEL_ALERT_EVENT_ID:
            raise RuntimeError("SENTINEL_FATAL: nil alert_event_id")
        symbol = event["symbol"]

        record = {
            "alert_event_id": alert_event_id,
            "symbol": symbol,
            "market": event["market"],
            "alert_type": event["alert_type"],
            "severity": event["severity"],
            "observation_start_at": _strip_tz(event["observation_start_at"]),
            "observation_end_at": _strip_tz(event["observation_end_at"]),
            "triggered_at": _strip_tz(event["triggered_at"]),
            "trigger_values": dict(event.get("trigger_values") or {}),
            "source_tick_event_id": event.get("source_tick_event_id"),
            "rule_name": event["rule_name"],
        }
        inserted = await self._alerts.upsert(record)
        if not inserted:
            logger.info("alert %s already exists; resuming fanout", alert_event_id)

        if self._fanout_fail_after_alert:
            raise RuntimeError("FANOUT_FAIL_AFTER_ALERT seam")

        user_ids = await self._watchlist.find_users_for_symbol(symbol)
        if not user_ids:
            logger.info("alert %s has no watchers for symbol=%s", alert_event_id, symbol)
            return

        pairs = await self._notifications.bulk_create_pending(
            user_ids, alert_event_id, symbol
        )

        payload_base = {
            "type": "alert",
            "alert_event_id": str(alert_event_id),
            "symbol": symbol,
            "market": event["market"],
            "alert_type": event["alert_type"],
            "severity": event["severity"],
            "observation_start_at": _isoformat(event["observation_start_at"]),
            "observation_end_at": _isoformat(event["observation_end_at"]),
            "triggered_at": _isoformat(event["triggered_at"]),
            "trigger_values": dict(event.get("trigger_values") or {}),
            "rule_name": event["rule_name"],
        }

        for user_id, notification_id in pairs:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            payload = {**payload_base, "notification_id": str(notification_id)}
            user_id_str = str(user_id)

            if not self._registry.is_connected(user_id_str):
                await self._notifications.mark_failed(notification_id, now, "no_connection")
                continue

            sent, _failed = await self._registry.send_to_user(user_id_str, payload)
            if sent > 0:
                await self._notifications.mark_sent(notification_id, now)
            else:
                await self._notifications.mark_failed(notification_id, now, "send_error")

    @staticmethod
    def _coerce_uuid(value: Any) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

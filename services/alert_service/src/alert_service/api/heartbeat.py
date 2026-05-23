"""Application-level WebSocket heartbeat.

Starlette does not send ping/pong frames automatically; we send a JSON
heartbeat every ``interval_seconds`` to detect dead connections, which the
registry then auto-removes (its send_to_user already removes failed sends).
"""
from __future__ import annotations

import asyncio
import logging

from alert_service.ws.registry import ConnectionRegistry


logger = logging.getLogger(__name__)


async def heartbeat_loop(registry: ConnectionRegistry, interval_seconds: float = 25.0) -> None:
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            users = list(registry._by_user.keys())
            for user_id in users:
                try:
                    await registry.send_to_user(user_id, {"type": "ping"})
                except Exception as exc:
                    logger.warning("heartbeat for user_id=%s failed: %s", user_id, exc)
    except asyncio.CancelledError:
        logger.info("heartbeat loop cancelled")
        raise

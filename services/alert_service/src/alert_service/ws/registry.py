"""In-memory WebSocket connection registry.

Tracks active WebSocket connections per user_id. v1 is single-instance only;
the trade-off table for multi-instance (Redis pub/sub vs ws-gateway) lives in
the design doc's Future Scaling section.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class WebSocketLike(Protocol):
    """Subset of starlette/fastapi WebSocket used by the registry."""

    async def send_json(self, data: Any) -> None: ...


class ConnectionRegistry:
    def __init__(self) -> None:
        self._by_user: dict[str, set[WebSocketLike]] = {}

    def add(self, user_id: str, ws: WebSocketLike) -> None:
        self._by_user.setdefault(user_id, set()).add(ws)

    def remove(self, user_id: str, ws: WebSocketLike) -> None:
        conns = self._by_user.get(user_id)
        if not conns:
            return
        conns.discard(ws)
        if not conns:
            self._by_user.pop(user_id, None)

    def get_connections(self, user_id: str) -> set[WebSocketLike]:
        return set(self._by_user.get(user_id, ()))

    def is_connected(self, user_id: str) -> bool:
        return bool(self._by_user.get(user_id))

    async def send_to_user(self, user_id: str, payload: dict[str, Any]) -> tuple[int, int]:
        conns = self.get_connections(user_id)
        sent = 0
        failed_ws: list[WebSocketLike] = []
        for ws in conns:
            try:
                await ws.send_json(payload)
                sent += 1
            except Exception as exc:
                logger.warning("send_json failed for user_id=%s: %s; removing connection", user_id, exc)
                failed_ws.append(ws)
        for ws in failed_ws:
            self.remove(user_id, ws)
        return sent, len(failed_ws)

    def total_connections(self) -> int:
        return sum(len(c) for c in self._by_user.values())

    def total_users(self) -> int:
        return len(self._by_user)

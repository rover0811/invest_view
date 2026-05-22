from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class ConnectionManager:

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        self._connections[user_id].discard(websocket)
        if not self._connections[user_id]:
            self._connections.pop(user_id, None)

    async def push(self, user_id: str, payload: dict[str, Any]) -> int:
        targets = list(self._connections.get(user_id, ()))
        delivered = 0
        for ws in targets:
            try:
                await ws.send_json(payload)
                delivered += 1
            except Exception:
                self._connections[user_id].discard(ws)
        return delivered

    def user_count(self) -> int:
        return len(self._connections)

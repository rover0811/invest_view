from types import TracebackType
from typing import Self

import websockets
from websockets.asyncio.client import ClientConnection

from kis_ingestion.models.requests import (
    SubscribeHeader,
    SubscribeInput,
    SubscribeMessage,
)


class KISWebSocketClient:
    def __init__(self, ws_url: str):
        self._ws_url: str = ws_url
        self._ws: ClientConnection | None = None
        self._connected: bool = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if self._connected:
            return

        self._ws = await websockets.connect(self._ws_url, ping_interval=None)
        self._connected = True

    async def disconnect(self) -> None:
        if self._ws is not None:
            await self._ws.close()

        self._ws = None
        self._connected = False

    async def send_subscribe(self, message: SubscribeMessage) -> None:
        ws = self._require_connection()
        await ws.send(message.to_wire())

    async def send_unsubscribe(self, approval_key: str, tr_id: str, symbol: str) -> None:
        message = SubscribeMessage(
            header=SubscribeHeader(approval_key=approval_key, tr_type="2"),
            body={"input": SubscribeInput(tr_id=tr_id, tr_key=symbol)},
        )
        await self.send_subscribe(message)

    async def recv(self) -> str:
        ws = self._require_connection()
        data = await ws.recv()
        return str(data)

    async def send_pong(self, data: str) -> None:
        ws = self._require_connection()
        await ws.pong(data)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.disconnect()

    def _require_connection(self) -> ClientConnection:
        if self._ws is None or not self._connected:
            raise RuntimeError("WebSocket is not connected")
        return self._ws

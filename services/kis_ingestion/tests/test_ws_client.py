# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportAny=false, reportUnknownArgumentType=false

import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kis_ingestion.models.requests import SubscribeMessage
from kis_ingestion.models.requests import SubscribeHeader, SubscribeInput
from kis_ingestion.ws_client import KISWebSocketClient


def make_connected_client(mock_ws: AsyncMock) -> KISWebSocketClient:
    client = KISWebSocketClient("ws://example.com")
    client._ws = mock_ws
    client._connected = True
    return client


@pytest.mark.asyncio
async def test_connect_sets_connected_true():
    mock_ws = AsyncMock()

    with patch(
        "kis_ingestion.ws_client.websockets.connect",
        new=AsyncMock(return_value=mock_ws),
    ) as mock_connect:
        client = KISWebSocketClient("ws://example.com")

        await client.connect()

        assert client.connected is True
        assert client._ws is mock_ws
        mock_connect.assert_awaited_once_with("ws://example.com", ping_interval=None)


def test_connect_does_not_accept_approval_key_argument():
    assert list(inspect.signature(KISWebSocketClient.connect).parameters) == ["self"]


@pytest.mark.asyncio
async def test_disconnect_sets_connected_false():
    mock_ws = AsyncMock()

    with patch(
        "kis_ingestion.ws_client.websockets.connect",
        new=AsyncMock(return_value=mock_ws),
    ):
        client = KISWebSocketClient("ws://example.com")
        await client.connect()

    await client.disconnect()

    assert client.connected is False
    assert client._ws is None
    mock_ws.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_subscribe_sends_wire_format():
    mock_ws = AsyncMock()
    client = make_connected_client(mock_ws)
    message = SubscribeMessage.subscribe("approval-key", "H0STCNT0", "005930")

    await client.send_subscribe(message)

    mock_ws.send.assert_awaited_once_with(message.to_wire())


@pytest.mark.asyncio
async def test_send_unsubscribe_uses_tr_type_two():
    mock_ws = AsyncMock()
    client = make_connected_client(mock_ws)

    await client.send_unsubscribe("approval-key", "H0STCNT0", "005930")

    expected = SubscribeMessage(
        header=SubscribeHeader(approval_key="approval-key", tr_type="2"),
        body={"input": SubscribeInput(tr_id="H0STCNT0", tr_key="005930")},
    )
    mock_ws.send.assert_awaited_once_with(expected.to_wire())


@pytest.mark.asyncio
async def test_recv_returns_received_message():
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(return_value="test_data")
    client = make_connected_client(mock_ws)

    result = await client.recv()

    assert result == "test_data"


@pytest.mark.asyncio
async def test_send_pong_sends_pong_data():
    mock_ws = AsyncMock()
    mock_ws.pong = AsyncMock()
    client = make_connected_client(mock_ws)

    await client.send_pong("PINGPONG")

    mock_ws.pong.assert_awaited_once_with("PINGPONG")

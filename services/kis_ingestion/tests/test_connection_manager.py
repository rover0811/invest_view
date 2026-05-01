# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportAny=false, reportUnknownArgumentType=false

import sys
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kis_ingestion.approval_key_manager import KISApprovalKeyManager
from kis_ingestion.connection_manager import KISConnectionManager
from kis_ingestion.market_session import KRXSessionAdapter, MarketSessionRouter, NXTSessionAdapter
from kis_ingestion.raw_parser import KISRawMessageParser
from kis_ingestion.subscription_pool import KISSubscriptionPool
from kis_ingestion.tick_parser import KISTickParser
from kis_ingestion.ws_client import KISWebSocketClient


def make_manager() -> tuple[
    KISConnectionManager,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    approval_key_manager = MagicMock(spec=KISApprovalKeyManager)
    approval_key_manager.get_approval_key = AsyncMock(return_value="approval-key")
    approval_key_manager.force_refresh = AsyncMock(return_value="refreshed-key")

    ws_client = MagicMock(spec=KISWebSocketClient)
    ws_client.connect = AsyncMock()
    ws_client.disconnect = AsyncMock()
    ws_client.send_subscribe = AsyncMock()
    ws_client.send_unsubscribe = AsyncMock()
    ws_client.recv = AsyncMock()
    ws_client.send_pong = AsyncMock()
    ws_client.connected = False

    subscription_pool = MagicMock(spec=KISSubscriptionPool)
    subscription_pool.diff = MagicMock(return_value=([], []))
    subscription_pool.confirm_subscribed = MagicMock()
    subscription_pool.confirm_unsubscribed = MagicMock()
    subscription_pool.switch_market = MagicMock()
    subscription_pool.clear_actual = MagicMock()

    raw_parser = MagicMock(spec=KISRawMessageParser)
    raw_parser.is_pingpong = MagicMock(return_value=False)
    raw_parser.is_json_response = MagicMock(return_value=False)
    raw_parser.parse_json_response = MagicMock(return_value={})
    raw_parser.parse = MagicMock(return_value=None)
    raw_parser.split_records = MagicMock(return_value=[])

    tick_parser = MagicMock(spec=KISTickParser)
    tick_parser.parse = MagicMock()

    market_router = MagicMock(spec=MarketSessionRouter)
    market_router.market_name = "KRX"
    market_router.tick_tr_id = "H0STCNT0"
    market_router.switch = MagicMock(return_value=("H0STCNT0", "H0NXCNT0"))

    manager = KISConnectionManager(
        approval_key_manager=approval_key_manager,
        ws_client=ws_client,
        subscription_pool=subscription_pool,
        raw_parser=raw_parser,
        tick_parser=tick_parser,
        market_router=market_router,
    )
    return (
        manager,
        approval_key_manager,
        ws_client,
        subscription_pool,
        raw_parser,
        tick_parser,
        market_router,
    )


@pytest.mark.asyncio
async def test_start_gets_approval_key_connects_and_subscribes():
    (
        manager,
        approval_key_manager,
        ws_client,
        subscription_pool,
        _,
        _,
        _,
    ) = make_manager()
    subscription_pool.diff.return_value = ([("H0STCNT0", "005930")], [])

    await manager.connect()

    approval_key_manager.get_approval_key.assert_awaited_once()
    ws_client.connect.assert_awaited_once_with()
    ws_client.send_subscribe.assert_awaited_once()
    subscribe_message = ws_client.send_subscribe.await_args.args[0]
    assert subscribe_message.header.approval_key == "approval-key"
    assert subscribe_message.body["input"].tr_id == "H0STCNT0"
    assert subscribe_message.body["input"].tr_key == "005930"


@pytest.mark.asyncio
async def test_stop_sets_running_false_and_disconnects():
    manager, _, ws_client, _, _, _, _ = make_manager()
    manager._running = True

    await manager.stop()

    assert manager._running is False
    ws_client.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_pingpong_sends_pong():
    manager, _, ws_client, _, raw_parser, _, _ = make_manager()
    raw_parser.is_pingpong.return_value = True

    await manager._handle_message('{"header":{"tr_id":"PINGPONG"}}')

    ws_client.send_pong.assert_awaited_once_with('{"header":{"tr_id":"PINGPONG"}}')


@pytest.mark.asyncio
async def test_handle_message_data_parses_tick_and_increments_sequence():
    manager, _, _, _, raw_parser, tick_parser, market_router = make_manager()
    market_router.market_name = "NXT"
    raw_parser.parse.return_value = SimpleNamespace(
        encrypted=False,
        tr_id="H0STCNT0",
        count=1,
        payload="payload",
    )
    raw_parser.split_records.return_value = [str(index) for index in range(46)]
    mock_tick = SimpleNamespace(
        market_session_code="0",
        symbol="005930",
        market="KRX",
        price=73100,
    )
    tick_parser.parse.return_value = mock_tick
    manager._handle_market_switch = AsyncMock()

    await manager._handle_message("raw-data")

    assert manager.sequence == 1
    assert len(manager._tick_buffer) == 1
    assert manager._tick_buffer[0] == (mock_tick, manager.session_id, 1)
    tick_parser.parse.assert_called_once()
    assert tick_parser.parse.call_args.kwargs["source_tr_id"] == "H0STCNT0"
    assert tick_parser.parse.call_args.kwargs["market"] == "KRX"
    manager._handle_market_switch.assert_awaited_once_with("0")


@pytest.mark.asyncio
async def test_reconnect_generates_new_session_resets_sequence_and_resubscribes():
    manager, approval_key_manager, ws_client, subscription_pool, _, _, _ = make_manager()
    manager._approval_key = "old-key"
    manager._sequence = 9
    manager._base_delay = 0
    previous_session_id = manager.session_id
    manager._subscribe_all = AsyncMock()

    with patch("kis_ingestion.connection_manager.asyncio.sleep", new=AsyncMock()):
        await manager._reconnect()

    approval_key_manager.force_refresh.assert_awaited_once()
    ws_client.connect.assert_awaited_once_with()
    subscription_pool.clear_actual.assert_called_once()
    manager._subscribe_all.assert_awaited_once()
    assert manager.sequence == 0
    assert manager.session_id != previous_session_id


@pytest.mark.parametrize(
    ("tr_id", "expected_adapter_type", "expected_market"),
    [
        ("H0STCNT0", KRXSessionAdapter, "KRX"),
        ("H0STASP0", KRXSessionAdapter, "KRX"),
        ("H0NXCNT0", NXTSessionAdapter, "NXT"),
        ("H0NXASP0", NXTSessionAdapter, "NXT"),
        ("UNKNOWN", None, None),
    ],
)
def test_resolve_market_adapter_from_tr_id(
    tr_id: str,
    expected_adapter_type: type[KRXSessionAdapter] | type[NXTSessionAdapter] | None,
    expected_market: str | None,
):
    manager, _, _, _, _, _, _ = make_manager()

    adapter = manager._resolve_market_adapter_from_tr_id(tr_id)

    if expected_adapter_type is None:
        assert adapter is None
        return

    assert adapter is not None
    assert isinstance(adapter, expected_adapter_type)
    assert adapter.market_name == expected_market


@pytest.mark.asyncio
@pytest.mark.parametrize("market_session_code", ["0", "1", "2", "UNKNOWN"])
async def test_handle_market_switch_is_conservative_for_unsupported_session_codes(
    market_session_code: str,
    caplog: pytest.LogCaptureFixture,
):
    manager, _, _, subscription_pool, _, _, market_router = make_manager()
    manager._subscribe_all = AsyncMock()

    with caplog.at_level("INFO"):
        await manager._handle_market_switch(market_session_code)

    market_router.switch.assert_not_called()
    subscription_pool.switch_market.assert_not_called()
    manager._subscribe_all.assert_not_awaited()
    assert "unsupported market session code" in caplog.text.lower()


@pytest.mark.asyncio
async def test_session_id_changes_on_each_reconnect():
    manager, _, _, _, _, _, _ = make_manager()
    manager._base_delay = 0
    manager._subscribe_all = AsyncMock()

    with patch("kis_ingestion.connection_manager.asyncio.sleep", new=AsyncMock()):
        first_session_id = manager.session_id
        await manager._reconnect()
        second_session_id = manager.session_id
        await manager._reconnect()
        third_session_id = manager.session_id

    assert first_session_id != second_session_id
    assert second_session_id != third_session_id


@pytest.mark.asyncio
async def test_subscribe_all_tracks_pending_control_intents():
    manager, _, ws_client, subscription_pool, _, _, _ = make_manager()
    manager._approval_key = "approval-key"
    manager._pending_control_ops = {}
    subscription_pool.diff.return_value = (
        [("H0STCNT0", "005930")],
        [("H0STCNT0", "000660")],
    )

    await manager._subscribe_all()

    ws_client.send_unsubscribe.assert_awaited_once_with(
        "approval-key", "H0STCNT0", "000660"
    )
    ws_client.send_subscribe.assert_awaited_once()
    assert manager._pending_control_ops == {
        ("H0STCNT0", "000660"): "unsubscribe",
        ("H0STCNT0", "005930"): "subscribe",
    }


@pytest.mark.asyncio
async def test_handle_json_response_uses_pending_unsubscribe_intent_when_ack_omits_tr_type():
    manager, _, _, subscription_pool, raw_parser, _, _ = make_manager()
    manager._pending_control_ops = {("H0STCNT0", "005930"): "unsubscribe"}
    raw_parser.parse_json_response.return_value = {
        "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
        "body": {"rt_cd": "0", "msg1": "UNSUBSCRIBE SUCCESS"},
    }

    await manager._handle_json_response(json.dumps(raw_parser.parse_json_response.return_value))

    subscription_pool.confirm_unsubscribed.assert_called_once_with(
        "H0STCNT0", "005930"
    )
    subscription_pool.confirm_subscribed.assert_not_called()
    assert manager._pending_control_ops == {}


@pytest.mark.asyncio
async def test_handle_json_response_uses_pending_subscribe_intent_when_ack_omits_tr_type():
    manager, _, _, subscription_pool, raw_parser, _, _ = make_manager()
    manager._pending_control_ops = {("H0STCNT0", "005930"): "subscribe"}
    raw_parser.parse_json_response.return_value = {
        "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
        "body": {"rt_cd": "0", "msg1": "SUBSCRIBE SUCCESS"},
    }

    await manager._handle_json_response(json.dumps(raw_parser.parse_json_response.return_value))

    subscription_pool.confirm_subscribed.assert_called_once_with(
        "H0STCNT0", "005930"
    )
    subscription_pool.confirm_unsubscribed.assert_not_called()
    assert manager._pending_control_ops == {}


@pytest.mark.asyncio
async def test_handle_json_response_converges_already_unsubscribed_code():
    manager, _, _, subscription_pool, raw_parser, _, _ = make_manager()
    raw_parser.parse_json_response.return_value = {
        "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
        "body": {
            "rt_cd": "1",
            "msg_cd": "OPSP0003",
            "msg1": "ALREADY UNSUBSCRIBED",
        },
    }

    await manager._handle_json_response(json.dumps(raw_parser.parse_json_response.return_value))

    subscription_pool.confirm_unsubscribed.assert_called_once_with(
        "H0STCNT0", "005930"
    )
    subscription_pool.confirm_subscribed.assert_not_called()


@pytest.mark.asyncio
async def test_handle_json_response_converges_already_subscribed_code():
    manager, _, _, subscription_pool, raw_parser, _, _ = make_manager()
    raw_parser.parse_json_response.return_value = {
        "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
        "body": {
            "rt_cd": "1",
            "msg_cd": "OPSP0002",
            "msg1": "ALREADY SUBSCRIBED",
        },
    }

    await manager._handle_json_response(json.dumps(raw_parser.parse_json_response.return_value))

    subscription_pool.confirm_subscribed.assert_called_once_with(
        "H0STCNT0", "005930"
    )
    subscription_pool.confirm_unsubscribed.assert_not_called()


@pytest.mark.asyncio
async def test_handle_json_response_preserves_pending_intent_on_unknown_nack():
    manager, _, _, subscription_pool, raw_parser, _, _ = make_manager()
    manager._pending_control_ops = {("H0STCNT0", "005930"): "subscribe"}
    raw_parser.parse_json_response.return_value = {
        "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
        "body": {
            "rt_cd": "1",
            "msg_cd": "OPSP9999",
            "msg1": "TEMPORARY FAILURE",
        },
    }

    await manager._handle_json_response(json.dumps(raw_parser.parse_json_response.return_value))

    subscription_pool.confirm_subscribed.assert_not_called()
    subscription_pool.confirm_unsubscribed.assert_not_called()
    assert manager._pending_control_ops == {("H0STCNT0", "005930"): "subscribe"}


@pytest.mark.asyncio
async def test_handle_json_response_does_not_classify_generic_sub_prefix_as_subscribe():
    manager, _, _, subscription_pool, raw_parser, _, _ = make_manager()
    raw_parser.parse_json_response.return_value = {
        "header": {"tr_id": "H0STCNT0", "tr_key": "005930"},
        "body": {
            "rt_cd": "0",
            "msg1": "SUBSCRIPTION LIMIT REACHED",
        },
    }

    await manager._handle_json_response(json.dumps(raw_parser.parse_json_response.return_value))

    subscription_pool.confirm_subscribed.assert_not_called()
    subscription_pool.confirm_unsubscribed.assert_not_called()


@pytest.mark.asyncio
async def test_connect_sets_session_and_subscribes():
    manager, _, ws_client, subscription_pool, _, _, _ = make_manager()
    subscription_pool.diff.return_value = ([("H0STCNT0", "005930")], [])

    await manager.connect()

    assert manager._running is True
    assert manager._sequence == 0
    assert manager._approval_key == "approval-key"
    ws_client.connect.assert_awaited_once()
    ws_client.send_subscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_iterator_yields_multiple_ticks_from_single_message():
    manager, _, ws_client, _, raw_parser, tick_parser, _ = make_manager()
    manager._running = True

    raw_parser.parse.return_value = SimpleNamespace(
        encrypted=False, tr_id="H0STCNT0", count=2, payload="payload"
    )
    raw_parser.split_records.return_value = [str(i) for i in range(92)]

    tick1 = SimpleNamespace(market_session_code="0", symbol="005930", market="KRX", price=73100)
    tick2 = SimpleNamespace(market_session_code="0", symbol="035720", market="KRX", price=52000)
    tick_parser.parse.side_effect = [tick1, tick2]
    manager._handle_market_switch = AsyncMock()

    call_count = 0
    async def mock_recv():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "raw-data"
        manager._running = False
        raise StopAsyncIteration
    ws_client.recv = mock_recv

    result1 = await manager.__anext__()
    result2 = await manager.__anext__()

    assert result1 == (tick1, manager.session_id, 1)
    assert result2 == (tick2, manager.session_id, 2)
    assert call_count == 1


@pytest.mark.asyncio
async def test_iterator_reconnects_with_new_session():
    manager, _, ws_client, _, raw_parser, tick_parser, _ = make_manager()
    manager._running = True
    manager._base_delay = 0

    raw_parser.parse.return_value = SimpleNamespace(
        encrypted=False, tr_id="H0STCNT0", count=1, payload="payload"
    )
    raw_parser.split_records.return_value = [str(i) for i in range(46)]
    tick_parser.parse.return_value = SimpleNamespace(
        market_session_code="0", symbol="005930", market="KRX", price=73100
    )
    manager._handle_market_switch = AsyncMock()

    import websockets.exceptions
    call_count = 0
    async def mock_recv():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise websockets.exceptions.ConnectionClosed(None, None)
        return "raw-data"
    ws_client.recv = mock_recv

    with patch("kis_ingestion.connection_manager.asyncio.sleep", new=AsyncMock()):
        manager._reconnect = AsyncMock(side_effect=lambda: setattr(manager, '_session_id', 'new-session'))
        result = await manager.__anext__()

    manager._reconnect.assert_awaited_once()
    assert result[1] == 'new-session'


@pytest.mark.asyncio
async def test_iterator_stops_cleanly_on_stop():
    manager, _, _, _, _, _, _ = make_manager()
    manager._running = False

    with pytest.raises(StopAsyncIteration):
        await manager.__anext__()



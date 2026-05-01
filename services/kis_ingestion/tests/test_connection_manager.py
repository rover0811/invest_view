# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportAny=false, reportUnknownArgumentType=false

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kis_ingestion.approval_key_manager import KISApprovalKeyManager
from kis_ingestion.connection_manager import KISConnectionManager
from kis_ingestion.market_session import MarketSessionRouter
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
    manager._receive_loop = AsyncMock()

    await manager.start()

    approval_key_manager.get_approval_key.assert_awaited_once()
    ws_client.connect.assert_awaited_once_with("approval-key")
    ws_client.send_subscribe.assert_awaited_once()
    subscribe_message = ws_client.send_subscribe.await_args.args[0]
    assert subscribe_message.header.approval_key == "approval-key"
    assert subscribe_message.body["input"].tr_id == "H0STCNT0"
    assert subscribe_message.body["input"].tr_key == "005930"
    manager._receive_loop.assert_awaited_once()


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
    raw_parser.parse.return_value = SimpleNamespace(
        encrypted=False,
        tr_id="H0STCNT0",
        count=1,
        payload="payload",
    )
    raw_parser.split_records.return_value = [str(index) for index in range(46)]
    tick_parser.parse.return_value = SimpleNamespace(
        market_session_code="0",
        symbol="005930",
        market="KRX",
        price=73100,
    )
    manager._handle_market_switch = AsyncMock()

    await manager._handle_message("raw-data")

    assert manager.sequence == 1
    tick_parser.parse.assert_called_once()
    assert tick_parser.parse.call_args.kwargs["source_tr_id"] == "H0STCNT0"
    assert tick_parser.parse.call_args.kwargs["market"] == market_router.market_name
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
    ws_client.connect.assert_awaited_once_with("refreshed-key")
    subscription_pool.clear_actual.assert_called_once()
    manager._subscribe_all.assert_awaited_once()
    assert manager.sequence == 0
    assert manager.session_id != previous_session_id


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

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportAny=false, reportUnknownArgumentType=false

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kis_ingestion.approval_key_manager import KISApprovalKeyManager
from kis_ingestion.connection_manager import KISConnectionManager, ReconnectExhaustedError
from kis_ingestion.market_session import MarketSessionRouter
from kis_ingestion.raw_parser import KISRawMessageParser
from kis_ingestion.subscription_pool import KISSubscriptionPool
from kis_ingestion.tick_parser import KISTickParser
from kis_ingestion.ws_client import KISWebSocketClient


def make_manager() -> tuple[KISConnectionManager, MagicMock, MagicMock]:
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

    tick_parser = MagicMock(spec=KISTickParser)
    tick_parser.parse = MagicMock()

    market_router = MagicMock(spec=MarketSessionRouter)
    market_router.market_name = "KRX"
    market_router.tick_tr_id = "H0STCNT0"

    manager = KISConnectionManager(
        approval_key_manager=approval_key_manager,
        ws_client=ws_client,
        subscription_pool=subscription_pool,
        raw_parser=raw_parser,
        tick_parser=tick_parser,
        market_router=market_router,
    )
    return manager, ws_client, approval_key_manager


@pytest.mark.asyncio
async def test_reconnect_backoff_is_exponential_with_jitter():
    """
    RED: sleep delays must follow exponential+jitter bands, not linear.

    Expected bands (base=1.0, jitter_max=base):
        attempt 1: [1.0, 2.0)
        attempt 2: [2.0, 3.0)
        attempt 3: [4.0, 5.0)
        attempt 4: [8.0, 9.0)
        attempt 5: [16.0, 17.0)

    Current LINEAR code yields 1,2,3,4,5 — FAILS at attempt 3 (3 < 4).
    """
    manager, ws_client, _ = make_manager()
    ws_client.connect.side_effect = ConnectionError("simulated connection failure")

    recorded_delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        recorded_delays.append(delay)

    with patch("kis_ingestion.connection_manager.asyncio.sleep", side_effect=capture_sleep):
        with pytest.raises(ReconnectExhaustedError):
            await manager._reconnect()

    assert len(recorded_delays) == manager._max_retries, (
        f"Expected {manager._max_retries} sleep calls, got {len(recorded_delays)}: {recorded_delays}"
    )

    base = manager._base_delay
    jitter_max = base

    for attempt_idx, actual_delay in enumerate(recorded_delays):
        attempt = attempt_idx + 1
        exp_base = base * (2 ** (attempt - 1))
        exp_ceiling = exp_base + jitter_max

        assert actual_delay >= exp_base, (
            f"Attempt {attempt}: delay {actual_delay:.3f} < exponential base {exp_base:.3f}. "
            f"Current code is LINEAR — expected RED failure."
        )
        assert actual_delay < exp_ceiling, (
            f"Attempt {attempt}: delay {actual_delay:.3f} >= ceiling {exp_ceiling:.3f}. "
            f"Jitter exceeded expected maximum."
        )


@pytest.mark.asyncio
async def test_reconnect_delays_are_not_linear():
    manager, ws_client, _ = make_manager()
    ws_client.connect.side_effect = ConnectionError("simulated connection failure")

    recorded_delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        recorded_delays.append(delay)

    with patch("kis_ingestion.connection_manager.asyncio.sleep", side_effect=capture_sleep):
        with pytest.raises(ReconnectExhaustedError):
            await manager._reconnect()

    base = manager._base_delay
    linear_sequence = [base * attempt for attempt in range(1, manager._max_retries + 1)]

    assert recorded_delays != linear_sequence, (
        f"Delays {recorded_delays} match the LINEAR sequence {linear_sequence}. "
        f"Backoff must be EXPONENTIAL, not linear."
    )

    if len(recorded_delays) >= 3:
        assert recorded_delays[2] >= base * 4, (
            f"Attempt 3 delay {recorded_delays[2]:.3f} < {base * 4:.3f} (exponential minimum). "
            f"Current linear code gives {base * 3:.3f} — RED as expected."
        )

from datetime import datetime, time
import pytest
from kis_ingestion.market_session import (
    KRXSessionAdapter,
    NXTSessionAdapter,
    MarketSessionRouter,
    ScheduleBasedSessionSwitcher,
    is_trading_window,
)


def test_krx_session_adapter():
    adapter = KRXSessionAdapter()
    assert adapter.tick_tr_id == "H0STCNT0"
    assert adapter.hoga_tr_id == "H0STASP0"
    assert adapter.market_name == "KRX"


def test_nxt_session_adapter():
    adapter = NXTSessionAdapter()
    assert adapter.tick_tr_id == "H0NXCNT0"
    assert adapter.hoga_tr_id == "H0NXASP0"
    assert adapter.market_name == "NXT"


def test_market_session_router_delegation():
    krx = KRXSessionAdapter()
    router = MarketSessionRouter(krx)
    assert router.active == krx
    assert router.tick_tr_id == "H0STCNT0"
    assert router.hoga_tr_id == "H0STASP0"
    assert router.market_name == "KRX"


def test_market_session_router_switch():
    krx = KRXSessionAdapter()
    nxt = NXTSessionAdapter()
    router = MarketSessionRouter(krx)

    old_tr, new_tr = router.switch(nxt)

    assert old_tr == "H0STCNT0"
    assert new_tr == "H0NXCNT0"
    assert router.active == nxt
    assert router.tick_tr_id == "H0NXCNT0"
    assert router.market_name == "NXT"


@pytest.mark.parametrize(
    "test_time, expected_market",
    [
        (time(8, 30), "NXT"),
        (time(10, 0), "KRX"),
        (time(16, 0), "NXT"),
        (time(7, 0), "KRX"),
        (time(21, 0), "KRX"),
    ],
)
def test_schedule_based_session_switcher(test_time, expected_market):
    switcher = ScheduleBasedSessionSwitcher()
    now = datetime.combine(datetime.today(), test_time)
    adapter = switcher.determine_initial_market(now)
    assert adapter.market_name == expected_market


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 10, 19, 7, 59), False),
        (datetime(2026, 10, 19, 8, 0), True),
        (datetime(2026, 10, 19, 19, 59), True),
        (datetime(2026, 10, 19, 20, 0), False),
        (datetime(2026, 10, 17, 10, 0), False),
    ],
)
def test_is_trading_window(now, expected):
    assert is_trading_window(now) is expected

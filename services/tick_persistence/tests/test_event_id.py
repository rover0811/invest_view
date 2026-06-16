from __future__ import annotations

import pytest

from tick_persistence.event_id import compute_event_id


def test_compute_event_id_matches_design_vector() -> None:
    tick = {
        "market": "KRX",
        "symbol": "005930",
        "business_date": "20260617",
        "cumulative_volume": 123456,
        "trade_time": "091530",
        "price": 70100,
        "trade_type": "2",
    }

    assert compute_event_id(tick) == "cc293f67-5c08-58c8-86fb-ef8835363c9c"


def test_compute_event_id_uses_source_tr_id_when_market_absent() -> None:
    tick = {
        "source_tr_id": "H0STCNT0",
        "symbol": "005930",
        "business_date": "20260617",
        "cumulative_volume": "123456",
        "trade_time": "091530",
        "price": "70100",
        "trade_type": "2",
    }

    assert compute_event_id(tick) == "722bfeb1-9b27-55af-aa06-e1f9960620c4"


def test_compute_event_id_rejects_missing_identity_field() -> None:
    tick = {
        "market": "KRX",
        "symbol": "005930",
        "business_date": "20260617",
        "cumulative_volume": 123456,
        "trade_time": "091530",
        "price": 70100,
    }

    with pytest.raises(ValueError, match="missing required tick identity field: trade_type"):
        compute_event_id(tick)

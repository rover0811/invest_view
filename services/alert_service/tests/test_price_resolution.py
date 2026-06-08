from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from alert_service.api.price_resolution import KST, daily_close_as_of, is_snapshot_fresh, resolve_price


def test_is_snapshot_fresh_accepts_recent_snapshot() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    assert is_snapshot_fresh(now - timedelta(seconds=120), 70000, now, 300) is True


def test_is_snapshot_fresh_rejects_expired_snapshot() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    assert is_snapshot_fresh(now - timedelta(seconds=301), 70000, now, 300) is False


def test_is_snapshot_fresh_rejects_future_beyond_skew() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    assert is_snapshot_fresh(now + timedelta(seconds=31), 70000, now, 300) is False


def test_is_snapshot_fresh_rejects_missing_price_or_timestamp() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    assert is_snapshot_fresh(now, None, now, 300) is False
    assert is_snapshot_fresh(None, 70000, now, 300) is False


def test_is_snapshot_fresh_treats_naive_updated_at_as_utc() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    assert is_snapshot_fresh(datetime(2026, 6, 4, 0, 59), 70000, now, 300) is True


def test_daily_close_as_of_uses_1530_kst() -> None:
    assert daily_close_as_of(date(2026, 6, 3)) == datetime(2026, 6, 3, 15, 30, tzinfo=KST)


def test_resolve_price_prefers_fresh_snapshot_and_keeps_realtime_fields() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    snapshot = {
        "symbol": "005930",
        "last_price": 72000,
        "change": 1500,
        "change_rate": 2.13,
        "change_sign": "2",
        "cumulative_volume": 123456789,
        "vi_trigger_price": 71000,
        "trading_halted": "1",
        "updated_at": now,
    }

    resolved = resolve_price(snapshot, {"symbol": "005930", "trade_date": date(2026, 6, 3), "close": 70000}, now, 300)

    assert resolved == {
        "symbol": "005930",
        "price": 72000,
        "source": "realtime_snapshot",
        "as_of": now,
        "is_realtime": True,
        "is_stale": False,
        "display_label": "실시간",
        "change": 1500,
        "change_rate": 2.13,
        "change_sign": "2",
        "cumulative_volume": 123456789,
        "vi_trigger_price": 71000,
        "trading_halted": "1",
    }


def test_resolve_price_uses_daily_for_stale_snapshot_and_clears_realtime_fields() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    resolved = resolve_price(
        {"symbol": "005930", "last_price": 72000, "updated_at": now - timedelta(seconds=600)},
        {"symbol": "005930", "trade_date": date(2026, 6, 3), "close": 70000},
        now,
        300,
    )

    assert resolved["source"] == "daily_close"
    assert resolved["price"] == 70000
    assert resolved["is_realtime"] is False
    assert resolved["is_stale"] is True
    assert resolved["display_label"] == "장마감 종가 기준"
    assert resolved["as_of"] == datetime(2026, 6, 3, 15, 30, tzinfo=KST)
    assert resolved["change"] is None
    assert resolved["change_rate"] is None
    assert resolved["change_sign"] is None
    assert resolved["cumulative_volume"] is None
    assert resolved["vi_trigger_price"] is None
    assert resolved["trading_halted"] is None


def test_resolve_price_uses_daily_for_missing_snapshot_without_stale_flag() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    resolved = resolve_price(
        None,
        {"symbol": "005930", "trade_date": date(2026, 6, 3), "close": 70000},
        now,
        300,
    )

    assert resolved["source"] == "daily_close"
    assert resolved["is_stale"] is False


def test_resolve_price_returns_none_when_no_sources() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    resolved = resolve_price(None, None, now, 300)

    assert resolved["symbol"] is None
    assert resolved["price"] is None
    assert resolved["source"] == "none"
    assert resolved["display_label"] == "데이터 없음"
    assert resolved["is_realtime"] is False
    assert resolved["is_stale"] is False


def test_resolve_price_treats_snapshot_without_last_price_as_stale() -> None:
    now = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
    resolved = resolve_price(
        {"symbol": "005930", "last_price": None, "updated_at": now},
        {"symbol": "005930", "trade_date": date(2026, 6, 3), "close": 70000},
        now,
        300,
    )

    assert resolved["source"] == "daily_close"
    assert resolved["is_stale"] is True

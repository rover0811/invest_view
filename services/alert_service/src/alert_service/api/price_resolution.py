from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
CLOCK_SKEW_TOLERANCE_SECONDS = 30


def daily_close_as_of(trade_date: date) -> datetime:
    return datetime.combine(trade_date, time(15, 30), tzinfo=KST)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_snapshot_fresh(
    snapshot_updated_at: datetime | None,
    last_price: int | None,
    now_utc: datetime,
    ttl_seconds: int,
) -> bool:
    if last_price is None or snapshot_updated_at is None:
        return False
    updated_at_utc = _utc(snapshot_updated_at)
    now = _utc(now_utc)
    return (
        updated_at_utc <= now + timedelta(seconds=CLOCK_SKEW_TOLERANCE_SECONDS)
        and now - updated_at_utc <= timedelta(seconds=ttl_seconds)
    )


def resolve_price(
    snapshot: dict[str, Any] | None,
    daily: dict[str, Any] | None,
    now_utc: datetime,
    ttl_seconds: int,
) -> dict[str, Any]:
    snapshot_exists = snapshot is not None
    if snapshot is not None and is_snapshot_fresh(
        snapshot.get("updated_at"), snapshot.get("last_price"), now_utc, ttl_seconds
    ):
        return {
            "symbol": snapshot.get("symbol"),
            "price": snapshot.get("last_price"),
            "source": "realtime_snapshot",
            "as_of": _utc(snapshot["updated_at"]),
            "is_realtime": True,
            "is_stale": False,
            "display_label": "실시간",
            "change": snapshot.get("change"),
            "change_rate": snapshot.get("change_rate"),
            "change_sign": snapshot.get("change_sign"),
            "cumulative_volume": snapshot.get("cumulative_volume"),
            "vi_trigger_price": snapshot.get("vi_trigger_price"),
            "trading_halted": snapshot.get("trading_halted"),
        }

    # FUTURE: KIS REST current price slot
    if daily is not None and daily.get("close") is not None:
        return {
            "symbol": daily.get("symbol"),
            "price": daily.get("close"),
            "source": "daily_close",
            "as_of": daily_close_as_of(daily["trade_date"]),
            "is_realtime": False,
            "is_stale": snapshot_exists,
            "display_label": "장마감 종가 기준",
            "change": None,
            "change_rate": None,
            "change_sign": None,
            "cumulative_volume": None,
            "vi_trigger_price": None,
            "trading_halted": None,
        }

    return {
        "symbol": snapshot.get("symbol") if snapshot is not None else None,
        "price": None,
        "source": "none",
        "as_of": None,
        "is_realtime": False,
        "is_stale": snapshot_exists,
        "display_label": "데이터 없음",
        "change": None,
        "change_rate": None,
        "change_sign": None,
        "cumulative_volume": None,
        "vi_trigger_price": None,
        "trading_halted": None,
    }

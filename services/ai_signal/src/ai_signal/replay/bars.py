"""KIS 1-minute bars for one trading day (TR FHKST03010230)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol

KST = timezone(timedelta(hours=9))
TR_ID = "FHKST03010230"
PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)
_MAX_PAGES = 20


class KisClient(Protocol):
    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Bar:
    t: datetime
    open: int
    high: int
    low: int
    close: int
    volume: int


def _row_to_bar(row: dict[str, Any]) -> Bar:
    t = datetime.strptime(row["stck_bsop_date"] + row["stck_cntg_hour"], "%Y%m%d%H%M%S")
    return Bar(
        t=t.replace(tzinfo=KST),
        open=int(row["stck_oprc"]),
        high=int(row["stck_hgpr"]),
        low=int(row["stck_lwpr"]),
        close=int(row["stck_prpr"]),
        volume=int(row["cntg_vol"]),
    )


def fetch_minute_bars(client: KisClient, symbol: str, day: date) -> list[Bar]:
    by_time: dict[datetime, Bar] = {}
    cursor = datetime.combine(day, SESSION_CLOSE)
    open_at = datetime.combine(day, SESSION_OPEN)
    for _ in range(_MAX_PAGES):
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": cursor.strftime("%H%M%S"),
            "FID_INPUT_DATE_1": day.strftime("%Y%m%d"),
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "",
        }
        rows = client.get(PATH, TR_ID, params).get("output2") or []
        bars = [_row_to_bar(r) for r in rows if r.get("stck_bsop_date") and r.get("stck_cntg_hour")]
        if not bars:
            break
        for bar in bars:
            local = bar.t.replace(tzinfo=None)
            if local.date() == day and SESSION_OPEN <= local.time() <= SESSION_CLOSE:
                by_time[bar.t] = bar
        oldest = min(b.t for b in bars).replace(tzinfo=None)
        if oldest <= open_at:
            break
        next_cursor = oldest - timedelta(minutes=1)
        if next_cursor >= cursor:
            break
        cursor = next_cursor
    return [by_time[t] for t in sorted(by_time)]

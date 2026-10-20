from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ai_signal.market_data.kis_client import KisGetter

DOMESTIC = "/uapi/domestic-stock/v1"
INDEX_DAILY_TR = "FHKUP03500100"
US_DAILY_TR = "HHDFS76240000"
MARKET_CAP_RANK_TR = "FHPST01740000"
INDEX_WINDOW_DAYS = 60


@dataclass(frozen=True)
class DailyClose:
    business_date: date
    close: Decimal


@dataclass(frozen=True)
class ConstituentWeight:
    ticker: str
    weight: Decimal
    market_cap_100m_krw: Decimal | None


def _ymd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _parse_ymd(value: str) -> date:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _windows(start: date, end: date, days: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


def _dedupe(rows: list[DailyClose], start: date, end: date) -> list[DailyClose]:
    by_date = {
        row.business_date: row for row in rows if start <= row.business_date <= end
    }
    return [by_date[d] for d in sorted(by_date)]


async def fetch_index_daily(
    client: KisGetter, code: str, start: date, end: date
) -> list[DailyClose]:
    rows: list[DailyClose] = []
    for window_start, window_end in _windows(start, end, INDEX_WINDOW_DAYS):
        body = await client.get(
            f"{DOMESTIC}/quotations/inquire-daily-indexchartprice",
            INDEX_DAILY_TR,
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": _ymd(window_start),
                "FID_INPUT_DATE_2": _ymd(window_end),
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        rows += [
            DailyClose(_parse_ymd(r["stck_bsop_date"]), Decimal(r["bstp_nmix_prpr"]))
            for r in body.get("output2") or []
            if r.get("stck_bsop_date") and r.get("bstp_nmix_prpr")
        ]
    return _dedupe(rows, start, end)


async def fetch_us_daily(
    client: KisGetter, ticker: str, exchange: str, start: date, end: date
) -> list[DailyClose]:
    rows: list[DailyClose] = []
    cursor = end
    while cursor >= start:
        body = await client.get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            US_DAILY_TR,
            {
                "AUTH": "",
                "EXCD": exchange,
                "SYMB": ticker,
                "GUBN": "0",
                "BYMD": _ymd(cursor),
                "MODP": "1",
            },
        )
        page = [
            DailyClose(_parse_ymd(r["xymd"]), Decimal(r["clos"]))
            for r in body.get("output2") or []
            if r.get("xymd") and r.get("clos")
        ]
        if not page:
            break
        rows += page
        earliest = min(row.business_date for row in page)
        if earliest >= cursor:
            break
        cursor = earliest - timedelta(days=1)
    return _dedupe(rows, start, end)


async def fetch_constituent_weights(
    client: KisGetter, index_code: str
) -> list[ConstituentWeight]:
    """Market cap ranking inside an index; ``weight`` is a 0-1 fraction."""
    body = await client.get(
        f"{DOMESTIC}/ranking/market-cap",
        MARKET_CAP_RANK_TR,
        {
            "fid_input_price_2": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": index_code,
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_vol_cnt": "",
        },
    )
    out: list[ConstituentWeight] = []
    for r in body.get("output") or []:
        ticker = r.get("mksc_shrn_iscd")
        ratio = r.get("mrkt_whol_avls_rlim")
        if not ticker or ratio in (None, ""):
            continue
        cap = r.get("stck_avls")
        out.append(
            ConstituentWeight(
                ticker=ticker,
                weight=Decimal(ratio) / Decimal(100),
                market_cap_100m_krw=Decimal(cap) if cap not in (None, "") else None,
            )
        )
    return out

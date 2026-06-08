from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Mapping
from datetime import date, datetime, timedelta
from typing import Protocol, TypeAlias, cast, runtime_checkable

OHLCRow: TypeAlias = dict[str, date | int | None]

KIS_DAILY_ITEMCHARTPRICE_URL = (
    "https://openapi.koreainvestment.com:9443"
    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
)

_TR_ID_DAILY_ITEMCHARTPRICE = "FHKST03010100"
_MARKET_DIV_CODE_STOCK = "J"

# KIS docs/examples vary in wording; confirm 0/1 against a live 1-symbol call before trusting adjusted prices.
_ADJUSTED_PRICE_PARAM_VALUE = "0"
_UNADJUSTED_PRICE_PARAM_VALUE = "1"

_WINDOW_DAYS: int = 100
_MIN_INTERVAL_SECONDS: float = 0.06
_MAX_RATE_LIMIT_RETRIES: int = 5
_INITIAL_RATE_LIMIT_BACKOFF_SECONDS: float = 0.5
_RATE_LIMIT_MSG_CD: str = "EGW00201"


class KISAPIError(RuntimeError):
    """Raised when KIS returns a non-success API payload."""


class KISRateLimitError(KISAPIError):
    """Raised for KIS gateway rate-limit responses so callers can back off."""


class _HTTPResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _HTTPClient(Protocol):
    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
    ) -> _HTTPResponse: ...


@runtime_checkable
class _TokenProvider(Protocol):
    def get_token(self) -> str | Awaitable[str]: ...


async def fetch_daily_ohlc(
    http_client: _HTTPClient,
    token: str,
    app_key: str,
    app_secret: str,
    symbol: str,
    period_div: str,
    start_date: date | str,
    end_date: date | str,
    adjusted: bool = True,
) -> list[OHLCRow]:
    """Fetch one KIS daily/weekly/monthly OHLC window and parse ``output2`` rows."""
    params = {
        "FID_COND_MRKT_DIV_CODE": _MARKET_DIV_CODE_STOCK,
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": _format_yyyymmdd(start_date),
        "FID_INPUT_DATE_2": _format_yyyymmdd(end_date),
        "FID_PERIOD_DIV_CODE": period_div,
        "FID_ORG_ADJ_PRC": _ADJUSTED_PRICE_PARAM_VALUE if adjusted else _UNADJUSTED_PRICE_PARAM_VALUE,
    }
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": _TR_ID_DAILY_ITEMCHARTPRICE,
        "custtype": "P",
    }

    response = await http_client.get(KIS_DAILY_ITEMCHARTPRICE_URL, headers=headers, params=params)
    payload = _as_mapping(response.json())
    if not _looks_like_kis_payload(payload):
        response.raise_for_status()
    _raise_for_kis_error(payload)
    return _parse_output2(payload.get("output2"))


async def fetch_all_history(
    http_client: _HTTPClient,
    token_provider_or_token: str | _TokenProvider,
    app_key: str,
    app_secret: str,
    symbol: str,
    period_div: str,
    years: int = 10,
    *,
    adjusted: bool = True,
    today: date | None = None,
    window_days: int = _WINDOW_DAYS,
) -> list[OHLCRow]:
    """Fetch history by date-window pagination.

    ``token_provider_or_token`` is intentionally duck-typed: pass a plain bearer
    token string for simple scripts/tests, or any object with ``get_token()``
    (for example the existing KISTokenManager) without importing across services.
    The token is resolved before each HTTP attempt so a manager can refresh during
    long one-shot backfills.
    """
    current_end = today or date.today()
    target_start = current_end - timedelta(days=years * 365)
    by_trade_date: dict[date, OHLCRow] = {}

    while current_end >= target_start:
        current_start = max(target_start, current_end - timedelta(days=window_days))
        rows = await _fetch_window_with_rate_limit_retry(
            http_client,
            token_provider_or_token,
            app_key,
            app_secret,
            symbol,
            period_div,
            current_start,
            current_end,
            adjusted,
        )
        if not rows:
            break

        for row in rows:
            trade_date = cast(date, row["trade_date"])
            _ = by_trade_date.setdefault(trade_date, row)

        oldest = min(cast(date, row["trade_date"]) for row in rows)
        current_end = oldest - timedelta(days=1)
        if current_end >= target_start and _MIN_INTERVAL_SECONDS > 0:
            await asyncio.sleep(_MIN_INTERVAL_SECONDS)

    return [by_trade_date[trade_date] for trade_date in sorted(by_trade_date, reverse=True)]


async def _fetch_window_with_rate_limit_retry(
    http_client: _HTTPClient,
    token_provider_or_token: str | _TokenProvider,
    app_key: str,
    app_secret: str,
    symbol: str,
    period_div: str,
    start_date: date,
    end_date: date,
    adjusted: bool,
) -> list[OHLCRow]:
    attempt: int = 0
    while True:
        token = await _resolve_token(token_provider_or_token)
        try:
            return await fetch_daily_ohlc(
                http_client,
                token,
                app_key,
                app_secret,
                symbol,
                period_div,
                start_date,
                end_date,
                adjusted=adjusted,
            )
        except KISRateLimitError:
            if attempt >= _MAX_RATE_LIMIT_RETRIES:
                raise
            backoff_seconds = _rate_limit_backoff_seconds(attempt)
            await asyncio.sleep(backoff_seconds)
            attempt += 1


async def _resolve_token(token_provider_or_token: str | _TokenProvider) -> str:
    if isinstance(token_provider_or_token, str):
        return token_provider_or_token
    token = token_provider_or_token.get_token()
    if inspect.isawaitable(token):
        token = await token
    return str(token)


def _rate_limit_backoff_seconds(attempt: int) -> float:
    backoff_seconds = _INITIAL_RATE_LIMIT_BACKOFF_SECONDS
    for _ in range(attempt):
        backoff_seconds *= 2.0
    return backoff_seconds


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _looks_like_kis_payload(payload: Mapping[str, object]) -> bool:
    return "rt_cd" in payload or "msg_cd" in payload or "msg1" in payload


def _raise_for_kis_error(payload: Mapping[str, object]) -> None:
    if payload.get("rt_cd") == "0":
        return
    msg_cd = str(payload.get("msg_cd") or "")
    msg1 = str(payload.get("msg1") or "")
    message = f"KIS daily OHLC request failed: msg_cd={msg_cd or '<missing>'}, msg1={msg1 or '<missing>'}"
    if msg_cd == _RATE_LIMIT_MSG_CD:
        raise KISRateLimitError(message)
    raise KISAPIError(message)


def _parse_output2(raw_rows: object) -> list[OHLCRow]:
    if not isinstance(raw_rows, list):
        return []
    rows: list[OHLCRow] = []
    for raw_row in cast(list[object], raw_rows):
        parsed = _parse_ohlc_row(raw_row)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _parse_ohlc_row(raw_row: object) -> OHLCRow | None:
    if not isinstance(raw_row, Mapping):
        return None
    row = cast(Mapping[str, object], raw_row)
    trade_date = _parse_trade_date(row.get("stck_bsop_date"))
    open_ = _to_int(row.get("stck_oprc"))
    high = _to_int(row.get("stck_hgpr"))
    low = _to_int(row.get("stck_lwpr"))
    close = _to_int(row.get("stck_clpr"))
    volume = _to_int(row.get("acml_vol"))
    if None in (trade_date, open_, high, low, close, volume):
        return None
    return {
        "trade_date": trade_date,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "trade_amount": _to_int(row.get("acml_tr_pbmn")),
    }


def _format_yyyymmdd(value: date | str) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    compact = value.replace("-", "")
    _ = datetime.strptime(compact, "%Y%m%d")
    return compact


def _parse_trade_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except ValueError:
        return None


def _to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None

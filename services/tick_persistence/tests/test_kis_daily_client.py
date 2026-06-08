from __future__ import annotations

# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false, reportPrivateLocalImportUsage=false

from collections.abc import Mapping
from datetime import date
from typing import cast, final

import pytest

from tick_persistence.kis import daily_client
from tick_persistence.kis.daily_client import KISAPIError, fetch_all_history, fetch_daily_ohlc

_Payload = dict[str, object]


@final
class _Response:
    def __init__(self, payload: _Payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> _Payload:
        return self._payload


@final
class _KISErrorHTTPResponse:
    def __init__(self, payload: _Payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        raise AssertionError("KIS error payload should be parsed before HTTP status is raised")

    def json(self) -> _Payload:
        return self._payload


@final
class _HTTPClient:
    def __init__(self, *payloads: _Payload) -> None:
        self._payloads: list[_Payload] = list(payloads)
        self.calls: list[dict[str, object]] = []

    async def get(self, url: str, *, headers: Mapping[str, str], params: Mapping[str, str]) -> _Response:
        self.calls.append({"url": url, "headers": headers, "params": params})
        if not self._payloads:
            raise AssertionError("unexpected extra GET call")
        return _Response(self._payloads.pop(0))


@final
class _HTTP500KISErrorClient:
    def __init__(self, *payloads: _Payload) -> None:
        self._payloads: list[_Payload] = list(payloads)
        self.calls: list[dict[str, object]] = []

    async def get(self, url: str, *, headers: Mapping[str, str], params: Mapping[str, str]) -> _Response | _KISErrorHTTPResponse:
        self.calls.append({"url": url, "headers": headers, "params": params})
        if not self._payloads:
            raise AssertionError("unexpected extra GET call")
        payload = self._payloads.pop(0)
        if payload.get("rt_cd") == "1":
            return _KISErrorHTTPResponse(payload)
        return _Response(payload)


def _ok(rows: list[dict[str, str]]) -> _Payload:
    return {"rt_cd": "0", "output2": rows}


def _row(day: str, close: str = "70500") -> dict[str, str]:
    return {
        "stck_bsop_date": day,
        "stck_oprc": "70000",
        "stck_hgpr": "71000",
        "stck_lwpr": "69000",
        "stck_clpr": close,
        "acml_vol": "12345",
        "acml_tr_pbmn": "987654321",
    }


async def test_fetch_daily_ohlc_builds_kis_request_and_parses_output2_rows() -> None:
    http = _HTTPClient(
        _ok(
            [
                _row("20260603"),
                {**_row("20260602"), "stck_clpr": ""},
                {**_row(""), "stck_clpr": "70400"},
                {**_row("20260601"), "acml_tr_pbmn": ""},
            ]
        )
    )

    rows = await fetch_daily_ohlc(
        http,
        "token-1",
        "app-key",
        "app-secret",
        "005930",
        "D",
        date(2026, 6, 1),
        date(2026, 6, 3),
    )

    assert rows == [
        {
            "trade_date": date(2026, 6, 3),
            "open": 70000,
            "high": 71000,
            "low": 69000,
            "close": 70500,
            "volume": 12345,
            "trade_amount": 987654321,
        },
        {
            "trade_date": date(2026, 6, 1),
            "open": 70000,
            "high": 71000,
            "low": 69000,
            "close": 70500,
            "volume": 12345,
            "trade_amount": None,
        },
    ]
    first_call = http.calls[0]
    assert first_call["url"] == daily_client.KIS_DAILY_ITEMCHARTPRICE_URL
    assert first_call["headers"] == {
        "authorization": "Bearer token-1",
        "appkey": "app-key",
        "appsecret": "app-secret",
        "tr_id": "FHKST03010100",
        "custtype": "P",
    }
    assert first_call["params"] == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
        "FID_INPUT_DATE_1": "20260601",
        "FID_INPUT_DATE_2": "20260603",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": daily_client._ADJUSTED_PRICE_PARAM_VALUE,
    }


async def test_fetch_all_history_shifts_windows_stops_on_empty_and_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_client, "_MIN_INTERVAL_SECONDS", 0)
    http = _HTTPClient(
        _ok([_row("20260603"), _row("20260501")]),
        _ok([_row("20260501"), _row("20260430")]),
        _ok([]),
    )

    rows = await fetch_all_history(
        http,
        "static-token",
        "app-key",
        "app-secret",
        "005930",
        "D",
        years=1,
        today=date(2026, 6, 4),
    )

    assert [row["trade_date"] for row in rows] == [date(2026, 6, 3), date(2026, 5, 1), date(2026, 4, 30)]
    assert [_date_2(call) for call in http.calls] == ["20260604", "20260430", "20260429"]
    assert len(http.calls) == 3


async def test_fetch_all_history_retries_egw00201_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(daily_client.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(daily_client, "_MIN_INTERVAL_SECONDS", 0)
    http = _HTTPClient(
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate exceeded"},
        _ok([_row("20260603")]),
        _ok([]),
    )

    rows = await fetch_all_history(
        http,
        "static-token",
        "app-key",
        "app-secret",
        "005930",
        "D",
        years=1,
        today=date(2026, 6, 4),
    )

    assert [row["trade_date"] for row in rows] == [date(2026, 6, 3)]
    assert sleeps == [daily_client._INITIAL_RATE_LIMIT_BACKOFF_SECONDS]
    assert len(http.calls) == 3


async def test_fetch_all_history_retries_egw00201_even_when_http_status_is_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(daily_client.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(daily_client, "_MIN_INTERVAL_SECONDS", 0)
    http = _HTTP500KISErrorClient(
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate exceeded"},
        _ok([_row("20260603")]),
        _ok([]),
    )

    rows = await fetch_all_history(
        http,
        "static-token",
        "app-key",
        "app-secret",
        "005930",
        "D",
        years=1,
        today=date(2026, 6, 4),
    )

    assert [row["trade_date"] for row in rows] == [date(2026, 6, 3)]
    assert sleeps == [daily_client._INITIAL_RATE_LIMIT_BACKOFF_SECONDS]
    assert len(http.calls) == 3


async def test_fetch_all_history_stops_retrying_egw00201_after_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(daily_client.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(daily_client, "_MAX_RATE_LIMIT_RETRIES", 2)
    http = _HTTPClient(
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "rate exceeded"},
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "still exceeded"},
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "still exceeded"},
    )

    with pytest.raises(KISAPIError, match="EGW00201"):
        _ = await fetch_all_history(
            http,
            "static-token",
            "app-key",
            "app-secret",
            "005930",
            "D",
            years=1,
            today=date(2026, 6, 4),
        )

    assert sleeps == [
        daily_client._INITIAL_RATE_LIMIT_BACKOFF_SECONDS,
        daily_client._INITIAL_RATE_LIMIT_BACKOFF_SECONDS * 2,
    ]
    assert len(http.calls) == 3


def _date_2(call: dict[str, object]) -> str:
    params = cast(Mapping[str, str], call["params"])
    value = params["FID_INPUT_DATE_2"]
    return value

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import numpy as np
import pandas as pd
import pytest

from ai_signal.market_data.__main__ import build_window, parse_args
from ai_signal.market_data.beta import SymbolHistory, estimate_symbol_betas
from ai_signal.market_data.config import MarketDataSettings
from ai_signal.market_data.fetch import (
    DailyClose,
    fetch_constituent_weights,
    fetch_index_daily,
    fetch_us_daily,
)
from ai_signal.market_data.job import (
    KRX_SESSION,
    US_SESSION,
    closed_sessions,
    next_run_at,
)
from ai_signal.market_data.kis_client import KisApiError, KisRestClient
from ai_signal.market_data.repository import (
    KST,
    SymbolRef,
    WeightSnapshot,
    index_dates,
)


class FakeKis:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def get(
        self, path: str, tr_id: str, params: dict[str, str]
    ) -> dict[str, Any]:
        self.calls.append((path, tr_id, params))
        return self.responder(path, tr_id, params)


def test_fetch_index_daily_windows_and_dedupes() -> None:
    def responder(_: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        assert tr_id == "FHKUP03500100"
        start = params["FID_INPUT_DATE_1"]
        return {
            "rt_cd": "0",
            "output2": [
                {"stck_bsop_date": start, "bstp_nmix_prpr": "2500.12"},
                {"stck_bsop_date": "20260105", "bstp_nmix_prpr": "2510.00"},
                {"stck_bsop_date": "", "bstp_nmix_prpr": ""},
            ],
        }

    fake = FakeKis(responder)
    rows = asyncio.run(
        fetch_index_daily(fake, "0001", date(2026, 1, 2), date(2026, 4, 1))
    )
    assert len(fake.calls) == 2
    assert [r.business_date for r in rows] == [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 3, 3),
    ]
    assert rows[0].close == Decimal("2500.12")


def test_fetch_us_daily_paginates_backwards_until_start() -> None:
    pages = {
        "20261016": [
            {"xymd": "20261016", "clos": "600.5"},
            {"xymd": "20261015", "clos": "599"},
        ],
        "20261014": [
            {"xymd": "20261014", "clos": "598"},
            {"xymd": "20261013", "clos": "597"},
        ],
    }

    def responder(_: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        assert tr_id == "HHDFS76240000"
        assert params["EXCD"] == "NAS"
        return {"rt_cd": "0", "output2": pages.get(params["BYMD"], [])}

    fake = FakeKis(responder)
    rows = asyncio.run(
        fetch_us_daily(fake, "SOXX", "NAS", date(2026, 10, 14), date(2026, 10, 16))
    )
    assert [r.business_date.day for r in rows] == [14, 15, 16]
    assert len(fake.calls) == 2


def test_fetch_constituent_weights_converts_percent() -> None:
    def responder(_: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        assert tr_id == "FHPST01740000"
        assert params["fid_input_iscd"] == "0013"
        return {
            "rt_cd": "0",
            "output": [
                {
                    "mksc_shrn_iscd": "000660",
                    "mrkt_whol_avls_rlim": "33.10",
                    "stck_avls": "1500000",
                },
                {
                    "mksc_shrn_iscd": "999999",
                    "mrkt_whol_avls_rlim": "",
                    "stck_avls": "1",
                },
            ],
        }

    rows = asyncio.run(fetch_constituent_weights(FakeKis(responder), "0013"))
    assert len(rows) == 1
    assert rows[0].weight == Decimal("0.331")
    assert rows[0].market_cap_100m_krw == Decimal(1500000)


def _client(
    handler: Any, sleeps: list[float]
) -> tuple[KisRestClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        base_url="https://kis.test", transport=httpx.MockTransport(handler)
    )

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return KisRestClient(
        http, "key", "secret", min_interval_s=0.3, sleep=fake_sleep
    ), http


def test_kis_client_reuses_token_and_retries_rate_limit() -> None:
    token_calls = 0
    responses = [
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "limit"},
        {"rt_cd": "0", "output": []},
        {"rt_cd": "0", "output": []},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/oauth2/tokenP":
            token_calls += 1
            body = json.loads(request.content)
            assert body["appkey"] == "key"
            return httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
        assert request.headers["authorization"] == "Bearer t"
        assert request.headers["tr_id"] == "TR"
        payload = responses.pop(0)
        return httpx.Response(500 if payload["rt_cd"] == "1" else 200, json=payload)

    sleeps: list[float] = []

    async def scenario() -> int:
        client, http = _client(handler, sleeps)
        async with http:
            await client.get("/p", "TR", {})
            await client.get("/p", "TR", {})
        return client.calls

    assert asyncio.run(scenario()) == 3
    assert token_calls == 1
    assert 1.0 in sleeps


def test_kis_client_raises_on_business_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
        return httpx.Response(
            200, json={"rt_cd": "1", "msg_cd": "EGW02004", "msg1": "x"}
        )

    async def scenario() -> None:
        client, http = _client(handler, [])
        async with http:
            await client.get("/p", "TR", {})

    with pytest.raises(KisApiError, match="EGW02004"):
        asyncio.run(scenario())


def _history(n: int, seed: int = 11, with_sector: bool = True) -> SymbolHistory:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-03-03", periods=n)
    spy_ret = rng.normal(0, 0.01, n)
    us_idx = idx - pd.Timedelta(days=1)
    spy = pd.Series(500 * np.cumprod(1 + spy_ret), index=us_idx)
    m_ret = 0.3 * np.roll(spy_ret, 0) + rng.normal(0, 0.01, n)
    market = pd.Series(2500 * np.cumprod(1 + m_ret), index=idx)
    s_ret = 0.8 * m_ret + rng.normal(0, 0.01, n)
    sector = pd.Series(1000 * np.cumprod(1 + s_ret), index=idx)
    r = 0.4 * spy_ret + 1.0 * m_ret + 0.5 * s_ret + rng.normal(0, 0.005, n)
    stock = pd.Series(50000 * np.cumprod(1 + r), index=idx)
    symbol = SymbolRef(uuid4(), "000660", "0013", None)
    anchor = index_dates(market)[-1]
    return SymbolHistory(
        symbol=symbol,
        stock_close=stock,
        market_close=market,
        spy_close=spy,
        market_weights=[WeightSnapshot(anchor, 0.02)],
        sector_close=sector if with_sector else None,
        sector_weights=[WeightSnapshot(anchor, 0.05)] if with_sector else None,
    )


def test_estimate_betas_uses_ols_with_enough_history() -> None:
    history = _history(220)
    targets = index_dates(history.market_close)[-5:]
    rows = estimate_symbol_betas(history, targets)
    assert [r.as_of_date for r in rows] == targets
    assert {r.method for r in rows} == {"OLS_4L"}
    last = rows[-1]
    assert last.n_obs == 120
    assert last.window_days == 120
    assert last.beta_sector >= 0
    assert last.beta_global_sector is None
    assert last.r2 is not None and 0 < last.r2 <= 1


def test_estimate_betas_falls_back_to_unit_with_short_history() -> None:
    history = _history(70, with_sector=False)
    targets = index_dates(history.market_close)[-3:]
    rows = estimate_symbol_betas(history, targets)
    assert {r.method for r in rows} == {"UNIT_4L"}
    assert rows[-1].beta_sector == 0.0
    assert rows[-1].beta_market == 1.0
    assert rows[-1].r2 is None


def test_next_run_at_skips_weekend() -> None:
    settings = MarketDataSettings(
        kis_app_key="k", kis_app_secret="s", run_at_kst=time(17, 0)
    )
    friday_evening = datetime(2026, 10, 16, 18, 0, tzinfo=KST)
    assert next_run_at(friday_evening, settings) == datetime(
        2026, 10, 19, 17, 0, tzinfo=KST
    )
    monday_morning = datetime(2026, 10, 19, 9, 0, tzinfo=KST).astimezone(UTC)
    assert next_run_at(monday_morning, settings) == datetime(
        2026, 10, 19, 17, 0, tzinfo=KST
    )


def test_cli_backfill_window() -> None:
    args = parse_args(["--start", "2026-09-01", "--end", "2026-10-16", "--backfill"])
    window = build_window(args, history_days=400, refresh_days=10)
    assert window.beta_start == date(2026, 9, 1)
    assert window.fetch_start == date(2026, 9, 1) - timedelta(days=400)
    assert window.fetch_weights


def test_closed_sessions_drops_unfinished_today() -> None:
    rows = [
        DailyClose(date(2026, 10, 16), Decimal(1)),
        DailyClose(date(2026, 10, 19), Decimal(2)),
    ]
    before_us_close = datetime(2026, 10, 19, 18, 0, tzinfo=KST)
    after_us_close = datetime(2026, 10, 20, 6, 0, tzinfo=KST)
    assert closed_sessions(rows, US_SESSION, before_us_close) == rows[:1]
    assert closed_sessions(rows, US_SESSION, after_us_close) == rows
    during_krx = datetime(2026, 10, 19, 14, 0, tzinfo=KST)
    after_krx = datetime(2026, 10, 19, 17, 0, tzinfo=KST)
    assert closed_sessions(rows, KRX_SESSION, during_krx) == rows[:1]
    assert closed_sessions(rows, KRX_SESSION, after_krx) == rows


def test_kis_client_waits_when_token_issue_is_limited() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/oauth2/tokenP":
            attempts += 1
            if attempts == 1:
                return httpx.Response(403, json={"error_code": "EGW00133"})
            return httpx.Response(200, json={"access_token": "t", "expires_in": 86400})
        return httpx.Response(200, json={"rt_cd": "0"})

    sleeps: list[float] = []

    async def scenario() -> None:
        client, http = _client(handler, sleeps)
        async with http:
            _ = await client.get("/p", "TR", {})

    asyncio.run(scenario())
    assert attempts == 2
    assert 61.0 in sleeps

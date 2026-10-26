from __future__ import annotations

import asyncio
import importlib
import zlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai_signal.market_data.job import RunWindow, run_once
from ai_signal.market_data.trading_calendar import (
    is_trading_day,
    previous_close_at,
    previous_trading_day,
    trading_days,
)
from alembic import command

PostgresContainer = importlib.import_module(
    "testcontainers.community.postgres"
).PostgresContainer

START = date(2025, 9, 1)
END = date(2026, 3, 31)
BETA_START = date(2026, 3, 2)
TICKERS = ("005930", "000660", "051910")
DOMESTIC_CODES = ("0001", "0008", "0013")


def _series(key: str, days: pd.DatetimeIndex, base: float) -> dict[str, float]:
    rng = np.random.default_rng(zlib.crc32(key.encode()))
    levels = base * np.cumprod(1 + rng.normal(0, 0.01, len(days)))
    return {
        d.strftime("%Y%m%d"): round(float(v), 2)
        for d, v in zip(days, levels, strict=True)
    }


KR_DAYS = pd.bdate_range(START, END)
US_DAYS = pd.bdate_range(START - timedelta(days=10), END)
INDEX_LEVELS = {code: _series(code, KR_DAYS, 2500.0) for code in DOMESTIC_CODES}
US_LEVELS = {t: _series(t, US_DAYS, 500.0) for t in ("SPY", "SOXX", "XLB")}


class FakeKis:
    def __init__(self) -> None:
        self.calls = 0

    async def get(
        self, path: str, tr_id: str, params: dict[str, str]
    ) -> dict[str, Any]:
        self.calls += 1
        if tr_id == "FHKUP03500100":
            levels = INDEX_LEVELS.get(params["FID_INPUT_ISCD"], {})
            lo, hi = params["FID_INPUT_DATE_1"], params["FID_INPUT_DATE_2"]
            rows = [
                {"stck_bsop_date": d, "bstp_nmix_prpr": str(v)}
                for d, v in sorted(levels.items(), reverse=True)
                if lo <= d <= hi
            ]
            return {"rt_cd": "0", "output2": rows}
        if tr_id == "HHDFS76240000":
            levels = US_LEVELS.get(params["SYMB"], {})
            page = [
                {"xymd": d, "clos": str(v)}
                for d, v in sorted(levels.items(), reverse=True)
                if d <= params["BYMD"]
            ][:100]
            return {"rt_cd": "0", "output2": page}
        if tr_id == "FHPST01740000":
            code = params["fid_input_iscd"]
            share = {"0001": "10.5", "0013": "40.0", "0008": "12.0"}.get(code, "1.0")
            return {
                "rt_cd": "0",
                "output": [
                    {
                        "mksc_shrn_iscd": t,
                        "mrkt_whol_avls_rlim": share,
                        "stck_avls": "1000",
                    }
                    for t in TICKERS
                ],
            }
        return {"rt_cd": "0", "output": [], "output2": []}


def _asyncpg_url(container: Any) -> str:
    return (
        container.get_connection_url()
        .replace("postgresql+psycopg2", "postgresql+asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


async def _seed_stock_closes(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE silver.symbol_daily_ohlc (
                    symbol text NOT NULL, interval text NOT NULL, trade_date date NOT NULL,
                    open integer, high integer, low integer, close integer,
                    volume bigint, trade_amount bigint, source text,
                    fetched_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (symbol, interval, trade_date))
                """
            )
        )
        for ticker in TICKERS:
            levels = _series(ticker, KR_DAYS, 50000.0)
            await conn.execute(
                text(
                    "INSERT INTO silver.symbol_daily_ohlc (symbol, interval, trade_date, close) "
                    "VALUES (:s, 'd', :d, :c)"
                ),
                [
                    {"s": ticker, "d": pd.Timestamp(d).date(), "c": int(v)}
                    for d, v in levels.items()
                ],
            )


async def _counts(engine: AsyncEngine) -> dict[str, Any]:
    queries = {
        "index_value": "SELECT count(*) FROM reference.index_value",
        "us_etf_daily": "SELECT count(*) FROM reference.us_etf_daily",
        "weights": "SELECT count(*) FROM reference.index_constituent_weight",
        "beta": "SELECT count(*) FROM gold.symbol_beta",
        "ols": "SELECT count(*) FROM gold.symbol_beta WHERE method = 'OLS_4L'",
        "max_known": "SELECT max(known_at) FROM reference.index_value",
        "etf_beta": (
            "SELECT count(*) FROM gold.symbol_beta b JOIN reference.entity_symbol s "
            "ON s.entity_id = b.symbol_entity_id "
            "WHERE s.ticker = '000660' AND b.beta_global_sector IS NOT NULL"
        ),
        "neg_sector": "SELECT count(*) FROM gold.symbol_beta WHERE beta_sector < 0",
        "domestic": (
            "SELECT count(*) FROM reference.entity_index WHERE index_kind = 'DOMESTIC_INDEX'"
        ),
    }
    async with engine.connect() as conn:
        return {k: await conn.scalar(text(q)) for k, q in queries.items()}


def test_run_once_loads_reference_and_betas_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_dir = Path(__file__).resolve().parents[1]
    config = Config(str(service_dir / "alembic.ini"))
    config.set_main_option("script_location", str(service_dir / "alembic"))

    with PostgresContainer("postgres:16-alpine") as container:
        url = _asyncpg_url(container)
        monkeypatch.setenv("AI_SIGNAL_DATABASE_URL", url)
        command.upgrade(config, "head")

        async def calendar(engine: AsyncEngine) -> dict[str, Any]:
            async with engine.connect() as conn:
                return {
                    "monday_prev": await previous_trading_day(conn, date(2026, 3, 2)),
                    "saturday": await is_trading_day(conn, date(2026, 2, 28)),
                    "friday": await is_trading_day(conn, date(2026, 2, 27)),
                    "first_prev": await previous_trading_day(conn, START),
                    "week": await trading_days(
                        conn, date(2026, 2, 23), date(2026, 3, 1)
                    ),
                    "close_at": await previous_close_at(conn, date(2026, 3, 2)),
                }

        async def scenario() -> tuple[
            dict[str, Any], dict[str, Any], int, dict[str, Any]
        ]:
            engine = create_async_engine(url)
            try:
                await _seed_stock_closes(engine)
                window = RunWindow(beta_start=BETA_START, end=END, fetch_start=START)
                fake = FakeKis()
                summary = await run_once(engine, fake, window, history_days=400)
                first = await _counts(engine)
                await run_once(engine, FakeKis(), window, history_days=400)
                second = await _counts(engine)
                cal = await calendar(engine)
                return first, second, summary.beta_rows, cal
            finally:
                await engine.dispose()

        first, second, beta_rows, cal = asyncio.run(scenario())

    targets = sum(1 for d in KR_DAYS if d.date() >= BETA_START)
    assert first["index_value"] == len(DOMESTIC_CODES) * len(KR_DAYS)
    assert first["us_etf_daily"] > 0
    assert first["weights"] == first["domestic"] * len(TICKERS)
    assert beta_rows == first["beta"] == len(TICKERS) * targets
    assert first["ols"] == first["beta"]
    assert first["etf_beta"] == targets
    assert first["neg_sector"] == 0
    assert second == first
    assert cal["monday_prev"] == date(2026, 2, 27)
    assert cal["saturday"] is False
    assert cal["friday"] is True
    assert cal["first_prev"] is None
    assert cal["week"] == [date(2026, 2, d) for d in range(23, 28)]
    assert cal["close_at"].isoformat() == "2026-02-27T15:30:00+09:00"

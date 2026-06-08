from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.api.routes.stock_info import _iso_or_str
from alert_service.db.session import create_engine, create_session_factory


@pytest.mark.unit
def test_iso_or_str_none():
    assert _iso_or_str(None) is None


@pytest.mark.unit
def test_iso_or_str_passthrough():
    assert _iso_or_str("1989-09-25") == "1989-09-25"


@pytest.mark.unit
def test_iso_or_str_date():
    assert _iso_or_str(date(1975, 6, 11)) == "1975-06-11"


pytestmark = pytest.mark.qa

_DROP = "DROP SCHEMA IF EXISTS reference, serving CASCADE"

_DDL = (
    "CREATE SCHEMA reference",
    "CREATE SCHEMA serving",
    """
    CREATE TABLE reference.bronze_market_ticker (
        ticker TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        market TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE reference.bronze_stock_overview (
        ticker TEXT PRIMARY KEY,
        company JSONB,
        market_value_krw BIGINT,
        list_date DATE
    )
    """,
    """
    CREATE TABLE reference.financial_metrics (
        ticker TEXT NOT NULL,
        stmt_type TEXT NOT NULL,
        period_type TEXT NOT NULL,
        period TEXT NOT NULL,
        item_name TEXT NOT NULL,
        value NUMERIC,
        unit TEXT,
        updated_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE serving.symbol_snapshot (
        symbol TEXT PRIMARY KEY,
        last_price INTEGER
    )
    """,
)


def _make_container(engine, session_factory):
    container = MagicMock()
    container.config.allow_origins = []
    container.engine = engine
    container.session_factory = session_factory
    return container


@pytest_asyncio.fixture
async def stock_info_env(postgres_container):
    url = (
        postgres_container.get_connection_url()
        .replace("postgresql+psycopg2", "postgresql+asyncpg")
        .replace("postgresql://", "postgresql+asyncpg://")
    )
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_DROP)
        for ddl in _DDL:
            await conn.exec_driver_sql(ddl)
    session_factory = create_session_factory(engine)
    app = create_app(_make_container(engine, session_factory))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory
    async with engine.begin() as conn:
        await conn.exec_driver_sql(_DROP)
    await engine.dispose()


async def _seed_market_ticker(session_factory, symbol="005930"):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO reference.bronze_market_ticker "
                "(ticker, company_name, market) VALUES (:ticker, :name, :market)"
            ),
            {"ticker": symbol, "name": "삼성전자", "market": "KOSPI"},
        )
        await session.commit()


async def _seed_overview(session_factory, symbol="005930"):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO reference.bronze_stock_overview "
                "(ticker, company, market_value_krw, list_date) "
                "VALUES (:ticker, (:company)::jsonb, :market_cap, :list_date)"
            ),
            {
                "ticker": symbol,
                "company": json.dumps(
                    {"industry": {"displayName": "반도체"}, "ceo": "한종희"}, ensure_ascii=False
                ),
                "market_cap": 430_000_000_000_000,
                "list_date": date(1975, 6, 11),
            },
        )
        await session.commit()


async def _seed_snapshot(session_factory, symbol="005930", last_price=72_000):
    async with session_factory() as session:
        await session.execute(
            text("INSERT INTO serving.symbol_snapshot (symbol, last_price) VALUES (:symbol, :last_price)"),
            {"symbol": symbol, "last_price": last_price},
        )
        await session.commit()


async def _seed_metric(session_factory, stmt_type, period, item, value, unit="KRW"):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO reference.financial_metrics "
                "(ticker, stmt_type, period_type, period, item_name, value, unit) "
                "VALUES ('005930', :stmt_type, 'Q', :period, :item, :value, :unit)"
            ),
            {
                "stmt_type": stmt_type,
                "period": period,
                "item": item,
                "value": Decimal(str(value)),
                "unit": unit,
            },
        )
        await session.commit()


async def _seed_base_financials(session_factory, eps: int | None = 3200):
    for period, revenue, operating, net_income, ebitda, period_eps in (
        ("2024Q2", 74_000_000, 10_000_000, 8_000_000, 12_000_000, eps),
        ("2024Q1", 70_000_000, 8_000_000, 6_000_000, 10_000_000, 2800),
    ):
        await _seed_metric(session_factory, "INC", period, "매출액(수익)", revenue, "백만원")
        await _seed_metric(session_factory, "INC", period, "영업이익", operating, "백만원")
        await _seed_metric(session_factory, "INC", period, "당기순이익", net_income, "백만원")
        await _seed_metric(session_factory, "INC", period, "*EBITDA", ebitda, "백만원")
        if period_eps is not None:
            await _seed_metric(session_factory, "INC", period, "*주당순이익", period_eps, "원")
    await _seed_metric(session_factory, "BAL", "2024Q2", "지배주주지분", 424_313_255_000, "천원")
    await _seed_metric(session_factory, "BAL", "2024Q2", "발행주식수", 6_735_613, "천주")
    await _seed_metric(session_factory, "CAS", "2024Q2", "영업활동현금흐름", 9_000_000, "백만원")


def _write_evidence(filename: str, body: dict[str, Any]):
    evidence_dir = Path(__file__).resolve().parents[3] / ".sisyphus" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / filename).write_text(
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


async def test_stock_info_happy_path(stock_info_env):
    client, sf = stock_info_env
    await _seed_market_ticker(sf)
    await _seed_overview(sf)
    await _seed_snapshot(sf, last_price=347_750)
    await _seed_base_financials(sf, eps=3200)

    resp = await client.get("/api/stock-info/005930?period_type=Q")

    assert resp.status_code == 200
    body = resp.json()
    _write_evidence("task-5-stockinfo-happy.txt", body)
    income = body["financials"]["income"]
    assert "매출액(수익)" in {row["item"] for row in income}
    revenue_periods = [row["period"] for row in income if row["item"] == "매출액(수익)"]
    assert revenue_periods == ["2024Q2", "2024Q1"]
    assert body["indicators"]["eps"] == 3200
    assert body["indicators"]["per"] == pytest.approx(347_750 / 3200)
    controlling_equity_thousand_krw = 424_313_255_000
    shares_outstanding_thousand_shares = 6_735_613
    bps = controlling_equity_thousand_krw / shares_outstanding_thousand_shares
    assert bps == pytest.approx(62_995.489)
    assert body["indicators"]["pbr"] == pytest.approx(347_750 / bps)
    assert body["indicators"]["pbr"] == pytest.approx(5.52, rel=0.01)
    assert body["meta"]["industry_name"] == "반도체"
    assert body["meta"]["stock_name"] == "삼성전자"


async def test_stock_info_eps_zero_returns_null_per(stock_info_env):
    client, sf = stock_info_env
    await _seed_market_ticker(sf)
    await _seed_overview(sf)
    await _seed_snapshot(sf, last_price=72_000)
    await _seed_base_financials(sf, eps=0)

    resp = await client.get("/api/stock-info/005930?period_type=Q")

    assert resp.status_code == 200
    body = resp.json()
    _write_evidence("task-5-stockinfo-eps0.txt", body)
    assert body["indicators"]["eps"] == 0
    assert body["indicators"]["per"] is None
    assert "EPS" in body["coverage_note"]


async def test_stock_info_missing_overview_keeps_financials(stock_info_env):
    client, sf = stock_info_env
    await _seed_market_ticker(sf)
    await _seed_snapshot(sf, last_price=72_000)
    await _seed_base_financials(sf, eps=3200)

    resp = await client.get("/api/stock-info/005930?period_type=Q")

    assert resp.status_code == 200
    body = resp.json()
    _write_evidence("task-5-stockinfo-no-overview.txt", body)
    assert body["meta"]["stock_name"] == "삼성전자"
    assert body["meta"]["industry_name"] == "미분류"
    assert body["meta"]["ceo_name"] is None
    assert body["financials"]["income"]
    assert "overview" in body["coverage_note"]

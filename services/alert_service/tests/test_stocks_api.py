from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.db.session import create_engine, create_session_factory


pytestmark = pytest.mark.qa

_DROP = "DROP SCHEMA IF EXISTS serving, reference CASCADE"

_DDL = (
    "CREATE SCHEMA serving",
    "CREATE SCHEMA reference",
    """
    CREATE TABLE serving.symbol_snapshot (
        symbol TEXT PRIMARY KEY,
        last_price INTEGER, change INTEGER, change_rate NUMERIC(18, 8),
        change_sign TEXT, cumulative_volume BIGINT, trade_strength NUMERIC(18, 8),
        vi_trigger_price INTEGER, trading_halted TEXT, last_trade_time TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE reference.bronze_market_ticker (
        ticker TEXT PRIMARY KEY,
        company_name TEXT,
        market TEXT
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
async def stocks_env(postgres_container):
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


async def _seed_snapshot(session_factory, symbol, last_price, change_rate, cumulative_volume):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO serving.symbol_snapshot "
                "(symbol, last_price, change, change_rate, change_sign, "
                "cumulative_volume, vi_trigger_price, trading_halted) "
                "VALUES (:symbol, :lp, :chg, :cr, :cs, :cv, :vi, :th)"
            ),
            {
                "symbol": symbol,
                "lp": last_price,
                "chg": 1500,
                "cr": Decimal(change_rate),
                "cs": "2",
                "cv": cumulative_volume,
                "vi": 71000,
                "th": "0",
            },
        )
        await session.commit()


async def _seed_ticker(session_factory, ticker, company_name, market):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO reference.bronze_market_ticker "
                "(ticker, company_name, market) "
                "VALUES (:ticker, :name, :market)"
            ),
            {"ticker": ticker, "name": company_name, "market": market},
        )
        await session.commit()


async def test_stocks_returns_joined_rows_ordered_by_volume_desc(stocks_env):
    client, sf = stocks_env
    await _seed_snapshot(sf, "005930", 72000, "1.23", cumulative_volume=100)
    await _seed_snapshot(sf, "000660", 180000, "2.50", cumulative_volume=999)
    await _seed_ticker(sf, "005930", "삼성전자", "KOSPI")
    await _seed_ticker(sf, "000660", "SK하이닉스", "KOSPI")

    resp = await client.get("/api/stocks")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert set(body[0].keys()) == {"code", "name", "market", "price", "change_rate"}
    assert [r["code"] for r in body] == ["000660", "005930"]
    assert body[0]["code"] == "000660"
    assert body[0]["name"] == "SK하이닉스"
    assert body[0]["market"] == "KOSPI"
    assert body[0]["price"] == 180000
    assert body[0]["change_rate"] == pytest.approx(2.50)
    assert body[1]["code"] == "005930"
    assert body[1]["name"] == "삼성전자"


async def test_stocks_excludes_snapshot_without_matching_ticker(stocks_env):
    client, sf = stocks_env
    await _seed_snapshot(sf, "005930", 72000, "1.23", cumulative_volume=500)
    await _seed_ticker(sf, "005930", "삼성전자", "KOSPI")
    await _seed_snapshot(sf, "999999", 5000, "0.10", cumulative_volume=900)

    resp = await client.get("/api/stocks")

    assert resp.status_code == 200
    body = resp.json()
    codes = [r["code"] for r in body]
    assert codes == ["005930"]
    assert "999999" not in codes


async def test_stocks_empty_when_no_snapshot_rows(stocks_env):
    client, _sf = stocks_env

    resp = await client.get("/api/stocks")

    assert resp.status_code == 200
    assert resp.json() == []

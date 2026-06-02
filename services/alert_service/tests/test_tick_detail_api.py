from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.db.session import create_engine, create_session_factory


pytestmark = pytest.mark.qa

_DROP = "DROP SCHEMA IF EXISTS bronze CASCADE"

_DDL = (
    "CREATE SCHEMA bronze",
    """
    CREATE TABLE bronze.tick_history (
        symbol TEXT NOT NULL,
        trade_strength NUMERIC(18, 8),
        buy_ratio NUMERIC(18, 8),
        net_buy_count INTEGER,
        buy_count INTEGER,
        sell_count INTEGER,
        total_buy_volume BIGINT,
        total_sell_volume BIGINT,
        ask_remain_1 BIGINT,
        bid_remain_1 BIGINT,
        total_ask_remain BIGINT,
        total_bid_remain BIGINT,
        volume_turnover NUMERIC(18, 8),
        vwap NUMERIC(20, 8),
        prev_day_volume_rate NUMERIC(18, 8),
        persisted_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)

_FIELDS = {
    "trade_strength",
    "buy_ratio",
    "net_buy_count",
    "buy_count",
    "sell_count",
    "total_buy_volume",
    "total_sell_volume",
    "ask_remain_1",
    "bid_remain_1",
    "total_ask_remain",
    "total_bid_remain",
    "volume_turnover",
    "vwap",
    "prev_day_volume_rate",
}

_NUMERIC_FIELDS = ("trade_strength", "buy_ratio", "volume_turnover", "vwap", "prev_day_volume_rate")


def _make_container(engine, session_factory):
    container = MagicMock()
    container.config.allow_origins = []
    container.engine = engine
    container.session_factory = session_factory
    return container


@pytest_asyncio.fixture
async def tick_env(postgres_container):
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


async def _seed_tick(session_factory, symbol, persisted_at, values):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO bronze.tick_history "
                "(symbol, persisted_at, trade_strength, buy_ratio, net_buy_count, "
                "buy_count, sell_count, total_buy_volume, total_sell_volume, "
                "ask_remain_1, bid_remain_1, total_ask_remain, total_bid_remain, "
                "volume_turnover, vwap, prev_day_volume_rate) "
                "VALUES (:symbol, :ts, :trade_strength, :buy_ratio, :net_buy_count, "
                ":buy_count, :sell_count, :total_buy_volume, :total_sell_volume, "
                ":ask_remain_1, :bid_remain_1, :total_ask_remain, :total_bid_remain, "
                ":volume_turnover, :vwap, :prev_day_volume_rate)"
            ),
            {"symbol": symbol, "ts": persisted_at, **values},
        )
        await session.commit()


_OLDER = {
    "trade_strength": Decimal("99.99999999"),
    "buy_ratio": Decimal("0.10000000"),
    "net_buy_count": 1,
    "buy_count": 2,
    "sell_count": 3,
    "total_buy_volume": 10,
    "total_sell_volume": 20,
    "ask_remain_1": 30,
    "bid_remain_1": 40,
    "total_ask_remain": 50,
    "total_bid_remain": 60,
    "volume_turnover": Decimal("1.11111111"),
    "vwap": Decimal("100.00000000"),
    "prev_day_volume_rate": Decimal("0.50000000"),
}

_NEWER = {
    "trade_strength": Decimal("123.45678900"),
    "buy_ratio": Decimal("0.55000000"),
    "net_buy_count": 42,
    "buy_count": 100,
    "sell_count": 58,
    "total_buy_volume": 1000000,
    "total_sell_volume": 900000,
    "ask_remain_1": 5000,
    "bid_remain_1": 4000,
    "total_ask_remain": 50000,
    "total_bid_remain": 40000,
    "volume_turnover": Decimal("12.34567800"),
    "vwap": Decimal("70250.12345678"),
    "prev_day_volume_rate": Decimal("1.23450000"),
}


async def test_tick_detail_returns_newest_row(tick_env):
    client, sf = tick_env
    older = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 6, 1, 9, 5, 0, tzinfo=timezone.utc)
    await _seed_tick(sf, "005930", newer, _NEWER)
    await _seed_tick(sf, "005930", older, _OLDER)

    resp = await client.get("/api/tick-detail/005930")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == _FIELDS
    assert body["trade_strength"] == pytest.approx(123.456789)
    assert body["buy_ratio"] == pytest.approx(0.55)
    assert body["net_buy_count"] == 42
    assert body["buy_count"] == 100
    assert body["sell_count"] == 58
    assert body["total_buy_volume"] == 1000000
    assert body["total_sell_volume"] == 900000
    assert body["ask_remain_1"] == 5000
    assert body["bid_remain_1"] == 4000
    assert body["total_ask_remain"] == 50000
    assert body["total_bid_remain"] == 40000
    assert body["volume_turnover"] == pytest.approx(12.345678)
    assert body["vwap"] == pytest.approx(70250.12345678)
    assert body["prev_day_volume_rate"] == pytest.approx(1.2345)
    for field in _NUMERIC_FIELDS:
        assert isinstance(body[field], float)


async def test_tick_detail_absent_returns_404(tick_env):
    client, _sf = tick_env

    resp = await client.get("/api/tick-detail/999999")

    assert resp.status_code == 404

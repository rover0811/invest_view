from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.db.session import create_engine, create_session_factory


pytestmark = pytest.mark.qa

KST = timezone(timedelta(hours=9))

_DROP = "DROP SCHEMA IF EXISTS alert_service, silver, serving, gold CASCADE"

_DDL = (
    "CREATE SCHEMA alert_service",
    "CREATE SCHEMA silver",
    "CREATE SCHEMA serving",
    "CREATE SCHEMA gold",
    """
    CREATE TABLE silver.symbol_5m_metrics (
        id BIGSERIAL PRIMARY KEY,
        symbol TEXT NOT NULL,
        bucket_start TIMESTAMPTZ NOT NULL,
        bucket_end TIMESTAMPTZ,
        open INTEGER, high INTEGER, low INTEGER, close INTEGER,
        volume BIGINT, vwap NUMERIC(20, 8), tick_count INTEGER,
        is_final BOOLEAN NOT NULL DEFAULT false,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT symbol_5m_metrics_symbol_bucket_uq UNIQUE (symbol, bucket_start)
    )
    """,
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
    CREATE TABLE alert_service.alert_events (
        alert_event_id UUID PRIMARY KEY,
        symbol TEXT NOT NULL, market TEXT, alert_type TEXT NOT NULL, severity TEXT,
        observation_start_at TIMESTAMPTZ, observation_end_at TIMESTAMPTZ,
        triggered_at TIMESTAMPTZ NOT NULL, trigger_values JSONB NOT NULL,
        source_tick_event_id TEXT, rule_name TEXT, received_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE gold.pattern_events (
        pattern_event_id UUID PRIMARY KEY,
        symbol TEXT NOT NULL, market TEXT, pattern_type TEXT NOT NULL,
        window_start TIMESTAMPTZ, window_end TIMESTAMPTZ,
        triggered_at TIMESTAMPTZ NOT NULL, trigger_values JSONB NOT NULL,
        strategy_name TEXT, source_tick_event_id TEXT, received_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE OR REPLACE VIEW serving.symbol_signal_timeline AS
    SELECT symbol, 'alert' AS event_kind, alert_type AS event_type,
           triggered_at::timestamptz AS triggered_at, trigger_values, severity
    FROM alert_service.alert_events
    UNION ALL
    SELECT symbol, 'pattern' AS event_kind, pattern_type AS event_type,
           triggered_at::timestamptz AS triggered_at, trigger_values, NULL::text AS severity
    FROM gold.pattern_events
    """,
)


def _make_container(engine, session_factory):
    container = MagicMock()
    container.config.allow_origins = []
    container.engine = engine
    container.session_factory = session_factory
    return container


@pytest_asyncio.fixture
async def chart_env(postgres_container):
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


def _epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


async def _seed_candle(session_factory, symbol, bucket_start, o, h, low, c):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO silver.symbol_5m_metrics "
                "(symbol, bucket_start, bucket_end, open, high, low, close, is_final) "
                "VALUES (:symbol, :bs, :be, :o, :h, :low, :c, true)"
            ),
            {
                "symbol": symbol,
                "bs": bucket_start,
                "be": bucket_start + timedelta(minutes=5),
                "o": o,
                "h": h,
                "low": low,
                "c": c,
            },
        )
        await session.commit()


async def _seed_snapshot(session_factory, symbol, last_price, change_rate):
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
                "cv": 123456789,
                "vi": 71000,
                "th": "0",
            },
        )
        await session.commit()


async def _seed_alert(session_factory, symbol, triggered_at, alert_type, values):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO alert_service.alert_events "
                "(alert_event_id, symbol, market, alert_type, severity, "
                "observation_start_at, observation_end_at, triggered_at, trigger_values, rule_name) "
                "VALUES (:id, :symbol, 'KRX', :atype, 'WARNING', :ts, :ts, :ts, (:tv)::jsonb, 'rule_x')"
            ),
            {
                "id": str(uuid.uuid4()),
                "symbol": symbol,
                "atype": alert_type,
                "ts": triggered_at,
                "tv": json.dumps(values),
            },
        )
        await session.commit()


async def _seed_pattern(session_factory, symbol, triggered_at, pattern_type, values):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO gold.pattern_events "
                "(pattern_event_id, symbol, market, pattern_type, triggered_at, "
                "trigger_values, strategy_name) "
                "VALUES (:id, :symbol, 'KRX', :ptype, :ts, (:tv)::jsonb, 'strat_x')"
            ),
            {
                "id": str(uuid.uuid4()),
                "symbol": symbol,
                "ptype": pattern_type,
                "ts": triggered_at,
                "tv": json.dumps(values),
            },
        )
        await session.commit()


async def test_candles_returns_ohlc_in_ascending_time(chart_env):
    client, sf = chart_env
    b0 = datetime(2026, 6, 1, 9, 0, 0, tzinfo=KST)
    b1 = datetime(2026, 6, 1, 9, 5, 0, tzinfo=KST)
    b2 = datetime(2026, 6, 1, 9, 10, 0, tzinfo=KST)
    await _seed_candle(sf, "005930", b2, 70200, 70500, 70100, 70300)
    await _seed_candle(sf, "005930", b0, 70000, 70500, 69800, 70100)
    await _seed_candle(sf, "005930", b1, 70100, 70400, 70000, 70200)

    resp = await client.get("/api/candles/005930?limit=10")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    times = [c["time"] for c in body]
    assert times == sorted(times)
    assert all(isinstance(c["time"], int) for c in body)
    assert body[0] == {
        "time": _epoch_seconds(b0),
        "open": 70000,
        "high": 70500,
        "low": 69800,
        "close": 70100,
    }
    assert body[2]["close"] == 70300


async def test_candles_time_is_utc_epoch_seconds_from_kst_bucket(chart_env):
    client, sf = chart_env
    bucket = datetime(2026, 6, 1, 9, 0, 0, tzinfo=KST)
    await _seed_candle(sf, "005930", bucket, 70000, 70000, 70000, 70000)

    resp = await client.get("/api/candles/005930")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    expected = int(datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    assert body[0]["time"] == expected
    assert body[0]["time"] == _epoch_seconds(bucket)


async def test_candles_unknown_symbol_returns_empty_list(chart_env):
    client, _sf = chart_env

    resp = await client.get("/api/candles/000000")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_snapshot_returns_serving_row(chart_env):
    client, sf = chart_env
    await _seed_snapshot(sf, "005930", 72000, "1.23")

    resp = await client.get("/api/snapshot/005930")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "symbol",
        "last_price",
        "change",
        "change_rate",
        "change_sign",
        "cumulative_volume",
        "vi_trigger_price",
        "trading_halted",
        "updated_at",
    }
    assert body["symbol"] == "005930"
    assert body["last_price"] == 72000
    assert body["change_rate"] == pytest.approx(1.23)
    assert body["cumulative_volume"] == 123456789
    assert body["updated_at"] is not None


async def test_snapshot_absent_returns_404(chart_env):
    client, _sf = chart_env

    resp = await client.get("/api/snapshot/000000")

    assert resp.status_code == 404


async def test_timeline_unions_alert_and_pattern_in_time_order(chart_env):
    client, sf = chart_env
    t_alert = datetime(2026, 6, 1, 9, 0, 0, tzinfo=KST)
    t_pattern = datetime(2026, 6, 1, 9, 5, 0, tzinfo=KST)
    await _seed_alert(sf, "005930", t_alert, "VI_IMMINENT", {"current_price": "72000"})
    await _seed_pattern(sf, "005930", t_pattern, "GOLDEN_CROSS", {"ma_short": "5", "ma_long": "20"})

    resp = await client.get("/api/timeline/005930?limit=50")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert [e["event_kind"] for e in body] == ["alert", "pattern"]
    assert body[0]["event_type"] == "VI_IMMINENT"
    assert body[1]["event_type"] == "GOLDEN_CROSS"
    assert [e["time"] for e in body] == [_epoch_seconds(t_alert), _epoch_seconds(t_pattern)]
    assert all(isinstance(e["time"], int) for e in body)
    assert body[0]["trigger_values"] == {"current_price": "72000"}
    assert body[1]["trigger_values"] == {"ma_short": "5", "ma_long": "20"}
    assert body[0]["triggered_at"].startswith("2026-06-01")


async def test_timeline_unknown_symbol_returns_empty_list(chart_env):
    client, _sf = chart_env

    resp = await client.get("/api/timeline/000000")

    assert resp.status_code == 200
    assert resp.json() == []

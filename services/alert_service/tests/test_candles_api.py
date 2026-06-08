from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.api.routes.candles import _price_stream_events
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
    CREATE TABLE silver.symbol_daily_ohlc (
        symbol TEXT NOT NULL,
        interval TEXT NOT NULL,
        trade_date DATE NOT NULL,
        open INTEGER, high INTEGER, low INTEGER, close INTEGER,
        volume BIGINT, trade_amount BIGINT, source TEXT,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT symbol_daily_ohlc_symbol_interval_date_uq UNIQUE (symbol, interval, trade_date)
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
    container.config.price_realtime_ttl_seconds = 300
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


async def _seed_daily(session_factory, symbol, iv, trade_date, o, h, low, c, volume=None):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO silver.symbol_daily_ohlc "
                "(symbol, interval, trade_date, open, high, low, close, volume, source) "
                "VALUES (:symbol, :iv, :td, :o, :h, :low, :c, :vol, 'kis')"
            ),
            {
                "symbol": symbol,
                "iv": iv,
                "td": trade_date,
                "o": o,
                "h": h,
                "low": low,
                "c": c,
                "vol": volume,
            },
        )
        await session.commit()


async def _seed_snapshot(session_factory, symbol, last_price, change_rate, updated_at=None):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO serving.symbol_snapshot "
                "(symbol, last_price, change, change_rate, change_sign, "
                "cumulative_volume, vi_trigger_price, trading_halted, updated_at) "
                "VALUES (:symbol, :lp, :chg, :cr, :cs, :cv, :vi, :th, :updated_at)"
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
                "updated_at": updated_at or datetime.now(timezone.utc),
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


async def test_candles_limit_returns_latest_5m_bars_in_ascending_time(chart_env):
    client, sf = chart_env
    start = datetime(2026, 6, 1, 9, 0, 0, tzinfo=KST)
    buckets = [start + timedelta(minutes=5 * i) for i in range(5)]
    for idx, bucket in enumerate(buckets):
        price = 70000 + idx * 100
        await _seed_candle(sf, "005930", bucket, price, price + 50, price - 50, price + 10)

    resp = await client.get("/api/candles/005930?limit=3")

    assert resp.status_code == 200
    body = resp.json()
    assert [c["time"] for c in body] == [_epoch_seconds(b) for b in buckets[-3:]]
    assert [c["time"] for c in body] == sorted(c["time"] for c in body)
    assert body[0]["close"] == 70210
    assert body[-1]["time"] == _epoch_seconds(buckets[-1])
    assert body[-1]["close"] == 70410


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


async def test_candles_explicit_5m_interval_is_regression_safe(chart_env):
    client, sf = chart_env
    bucket = datetime(2026, 6, 1, 9, 0, 0, tzinfo=KST)
    await _seed_candle(sf, "005930", bucket, 70000, 70500, 69800, 70100)

    default_resp = await client.get("/api/candles/005930")
    explicit_resp = await client.get("/api/candles/005930?interval=5m")

    assert default_resp.status_code == 200
    assert explicit_resp.status_code == 200
    assert explicit_resp.json() == default_resp.json()
    body = explicit_resp.json()
    assert len(body) == 1
    assert set(body[0].keys()) == {"time", "open", "high", "low", "close"}
    assert body[0]["time"] == _epoch_seconds(bucket)


async def test_candles_daily_interval_queries_daily_table(chart_env):
    client, sf = chart_env
    await _seed_daily(sf, "005930", "d", date(2026, 6, 3), 700, 720, 690, 710, volume=300)
    await _seed_daily(sf, "005930", "d", date(2026, 6, 1), 600, 650, 590, 640, volume=100)
    await _seed_daily(sf, "005930", "d", date(2026, 6, 2), 640, 660, 630, 655, volume=200)
    await _seed_daily(sf, "005930", "w", date(2026, 6, 1), 1000, 1100, 990, 1080, volume=900)
    await _seed_daily(sf, "005930", "m", date(2026, 5, 1), 5000, 5200, 4900, 5100, volume=9000)

    d_resp = await client.get("/api/candles/005930?interval=1d")
    w_resp = await client.get("/api/candles/005930?interval=1w")
    m_resp = await client.get("/api/candles/005930?interval=1M")

    assert d_resp.status_code == w_resp.status_code == m_resp.status_code == 200

    d_body = d_resp.json()
    assert len(d_body) == 3
    assert [c["time"] for c in d_body] == sorted(c["time"] for c in d_body)
    assert all(isinstance(c["time"], int) for c in d_body)
    assert set(d_body[0].keys()) == {"time", "open", "high", "low", "close", "volume"}
    assert d_body[0] == {
        "time": _epoch_seconds(datetime(2026, 6, 1, 0, 0, 0, tzinfo=KST)),
        "open": 600,
        "high": 650,
        "low": 590,
        "close": 640,
        "volume": 100,
    }
    assert d_body[2]["close"] == 710

    w_body = w_resp.json()
    m_body = m_resp.json()
    assert len(w_body) == 1
    assert len(m_body) == 1
    assert w_body[0]["close"] == 1080
    assert m_body[0]["close"] == 5100
    assert m_body[0]["time"] == _epoch_seconds(datetime(2026, 5, 1, 0, 0, 0, tzinfo=KST))


async def test_candles_limit_returns_latest_daily_bars_in_ascending_time(chart_env):
    client, sf = chart_env
    dates = [date(2026, 6, day) for day in range(1, 6)]
    for idx, trade_date in enumerate(dates):
        price = 600 + idx * 10
        await _seed_daily(
            sf,
            "005930",
            "d",
            trade_date,
            price,
            price + 20,
            price - 10,
            price + 5,
            volume=100 + idx,
        )

    resp = await client.get("/api/candles/005930?interval=1d&limit=3")

    assert resp.status_code == 200
    body = resp.json()
    expected_times = [
        _epoch_seconds(datetime(2026, 6, day, 0, 0, 0, tzinfo=KST)) for day in range(3, 6)
    ]
    assert [c["time"] for c in body] == expected_times
    assert [c["time"] for c in body] == sorted(c["time"] for c in body)
    assert body[0]["close"] == 625
    assert body[0]["volume"] == 102
    assert body[-1]["time"] == expected_times[-1]
    assert body[-1]["close"] == 645


async def test_candles_daily_time_is_kst_midnight_epoch_seconds(chart_env):
    client, sf = chart_env
    await _seed_daily(sf, "005930", "d", date(2026, 6, 1), 600, 650, 590, 640)

    resp = await client.get("/api/candles/005930?interval=1d")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    expected = int(datetime(2026, 5, 31, 15, 0, 0, tzinfo=timezone.utc).timestamp())
    assert body[0]["time"] == expected
    assert body[0]["time"] == _epoch_seconds(datetime(2026, 6, 1, 0, 0, 0, tzinfo=KST))
    assert isinstance(body[0]["time"], int)


async def test_candles_invalid_interval_returns_422(chart_env):
    client, _sf = chart_env

    for bad in ("1h", "bogus", "D", "5M", ""):
        resp = await client.get(f"/api/candles/005930?interval={bad}")
        assert resp.status_code == 422, bad


async def test_candles_empty_daily_table_returns_empty_list(chart_env):
    client, _sf = chart_env

    for iv in ("1d", "1w", "1M"):
        resp = await client.get(f"/api/candles/005930?interval={iv}")
        assert resp.status_code == 200, iv
        assert resp.json() == [], iv


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


async def test_price_endpoint_fresh_snapshot_returns_realtime(chart_env):
    client, sf = chart_env
    now = datetime.now(timezone.utc)
    await _seed_daily(sf, "005930", "d", date(2026, 6, 3), 69000, 71000, 68000, 70000)
    await _seed_snapshot(sf, "005930", 72000, "1.23", updated_at=now)

    resp = await client.get("/api/price/005930")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "005930"
    assert body["price"] == 72000
    assert body["source"] == "realtime_snapshot"
    assert body["is_realtime"] is True
    assert body["is_stale"] is False
    assert body["display_label"] == "실시간"
    assert body["change"] == 1500
    assert body["change_rate"] == pytest.approx(1.23)
    assert body["cumulative_volume"] == 123456789
    assert body["vi_trigger_price"] == 71000
    assert body["trading_halted"] == "0"
    assert body["as_of"] is not None


async def test_price_endpoint_stale_snapshot_falls_back_to_daily(chart_env):
    client, sf = chart_env
    await _seed_daily(sf, "005930", "d", date(2026, 6, 3), 69000, 71000, 68000, 70000)
    await _seed_snapshot(
        sf,
        "005930",
        72000,
        "1.23",
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=600),
    )

    resp = await client.get("/api/price/005930")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "005930"
    assert body["price"] == 70000
    assert body["source"] == "daily_close"
    assert body["is_realtime"] is False
    assert body["is_stale"] is True
    assert body["display_label"] == "장마감 종가 기준"
    assert body["as_of"] == "2026-06-03T15:30:00+09:00"
    assert body["change"] is None
    assert body["change_rate"] is None
    assert body["change_sign"] is None
    assert body["cumulative_volume"] is None
    assert body["vi_trigger_price"] is None
    assert body["trading_halted"] is None


async def test_price_endpoint_missing_snapshot_uses_daily_without_stale_flag(chart_env):
    client, sf = chart_env
    await _seed_daily(sf, "005930", "d", date(2026, 6, 3), 69000, 71000, 68000, 70000)

    resp = await client.get("/api/price/005930")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "005930"
    assert body["price"] == 70000
    assert body["source"] == "daily_close"
    assert body["is_realtime"] is False
    assert body["is_stale"] is False
    assert body["display_label"] == "장마감 종가 기준"


async def test_price_endpoint_no_sources_returns_none_contract(chart_env):
    client, _sf = chart_env

    resp = await client.get("/api/price/000000")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "000000"
    assert body["price"] is None
    assert body["source"] == "none"
    assert body["display_label"] == "데이터 없음"
    assert body["is_realtime"] is False
    assert body["is_stale"] is False
    assert body["as_of"] is None


async def test_price_stream_emits_initial_resolved_price_frame(chart_env):
    _client, sf = chart_env
    now = datetime.now(timezone.utc)
    await _seed_daily(sf, "005930", "d", date(2026, 6, 3), 69000, 71000, 68000, 70000)
    await _seed_snapshot(sf, "005930", 72000, "1.23", updated_at=now)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(session_factory=sf, config=SimpleNamespace(price_realtime_ttl_seconds=300))),
        is_disconnected=AsyncMock(return_value=False),
    )
    stream = _price_stream_events(request, "005930", poll_seconds=0.01)
    try:
        frame = await anext(stream)
    finally:
        await stream.aclose()

    assert frame.startswith("event: price\n")
    assert "\ndata: " in frame
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["symbol"] == "005930"
    assert payload["price"] == 72000
    assert payload["source"] == "realtime_snapshot"
    assert payload["display_label"] == "실시간"


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

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.db.session import create_engine, create_session_factory
from alert_service.signal_query import get_current_signal, get_signal_history


pytestmark = pytest.mark.qa

_DROP = "DROP SCHEMA IF EXISTS serving CASCADE"

_DDL = (
    "CREATE SCHEMA serving",
    """
    CREATE TABLE serving.symbol_signal (
        symbol TEXT PRIMARY KEY, move_id UUID NOT NULL, version INTEGER NOT NULL,
        status TEXT NOT NULL, headline_label TEXT, updated_at TIMESTAMPTZ NOT NULL,
        move_version INTEGER NOT NULL, move_window_end TIMESTAMPTZ NOT NULL,
        generation_method TEXT, symbol_name TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE serving.symbol_signal_cause (
        symbol TEXT REFERENCES serving.symbol_signal (symbol) ON DELETE CASCADE,
        rank SMALLINT, explanation TEXT NOT NULL, source_count INTEGER NOT NULL,
        PRIMARY KEY (symbol, rank)
    )
    """,
    """
    CREATE TABLE serving.symbol_signal_cause_source (
        symbol TEXT, rank SMALLINT, document_id UUID,
        PRIMARY KEY (symbol, rank, document_id),
        FOREIGN KEY (symbol, rank)
            REFERENCES serving.symbol_signal_cause (symbol, rank) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE serving.symbol_signal_history (
        move_id UUID, version INTEGER, symbol TEXT NOT NULL,
        move_version INTEGER NOT NULL, move_window_end TIMESTAMPTZ NOT NULL,
        status TEXT NOT NULL, headline_label TEXT, generation_method TEXT,
        updated_at TIMESTAMPTZ NOT NULL, symbol_name TEXT NOT NULL,
        PRIMARY KEY (move_id, version)
    )
    """,
    """
    CREATE TABLE serving.symbol_signal_history_cause (
        move_id UUID, version INTEGER, rank SMALLINT,
        explanation TEXT NOT NULL, source_count INTEGER NOT NULL,
        PRIMARY KEY (move_id, version, rank),
        FOREIGN KEY (move_id, version)
            REFERENCES serving.symbol_signal_history (move_id, version) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE serving.symbol_signal_history_cause_source (
        move_id UUID, version INTEGER, rank SMALLINT, document_id UUID,
        PRIMARY KEY (move_id, version, rank, document_id),
        FOREIGN KEY (move_id, version, rank)
            REFERENCES serving.symbol_signal_history_cause (move_id, version, rank)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE serving.symbol_snapshot (
        symbol TEXT PRIMARY KEY, change_rate NUMERIC(18, 8),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)

SYMBOL = "005930"
KST = timezone(timedelta(hours=9))
DAY = date(2026, 3, 30)
CLOSE = datetime(2026, 3, 30, 15, 30, tzinfo=KST)
UPDATED = datetime(2026, 3, 30, 16, 0, tzinfo=KST)
DOC_A = UUID("00000000-0000-4000-8000-00000000000a")
DOC_B = UUID("00000000-0000-4000-8000-00000000000b")
DOC_C = UUID("00000000-0000-4000-8000-00000000000c")
DOC_D = UUID("00000000-0000-4000-8000-00000000000d")


def _make_container(engine, session_factory):
    container = MagicMock()
    container.config.allow_origins = []
    container.engine = engine
    container.session_factory = session_factory
    return container


@pytest_asyncio.fixture
async def signal_env(postgres_container):
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


def _row(move_id: UUID, move_version: int, version: int, window_end: datetime, status: str):
    return {
        "symbol": SYMBOL,
        "symbol_name": "삼성전자",
        "move_id": move_id,
        "move_version": move_version,
        "version": version,
        "move_window_end": window_end,
        "status": status,
        "label": "AI 수익성 우려" if status == "SHOWN" else None,
        "method": "llm" if status == "SHOWN" else None,
        "updated_at": UPDATED,
    }


async def _seed(session_factory) -> tuple[UUID, UUID]:
    move_day, move_next = uuid4(), uuid4()
    history = [
        _row(move_day, 1, 1, CLOSE - timedelta(hours=2), "HIDDEN"),
        _row(move_day, 2, 2, CLOSE, "SHOWN"),
        _row(move_next, 1, 1, CLOSE + timedelta(days=1), "NO_CANDIDATE"),
    ]
    current = _row(move_day, 2, 2, CLOSE, "SHOWN")
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO serving.symbol_signal_history (move_id, version, symbol, "
                "move_version, move_window_end, status, headline_label, "
                "generation_method, updated_at, symbol_name) VALUES (:move_id, "
                ":version, :symbol, :move_version, :move_window_end, :status, :label, "
                ":method, :updated_at, :symbol_name)"
            ),
            history,
        )
        await session.execute(
            text(
                "INSERT INTO serving.symbol_signal_history_cause "
                "(move_id, version, rank, explanation, source_count) "
                "VALUES (:move_id, 2, :rank, :explanation, :count)"
            ),
            [
                {"move_id": move_day, "rank": 2, "explanation": "second", "count": 1},
                {"move_id": move_day, "rank": 1, "explanation": "first", "count": 3},
            ],
        )
        await session.execute(
            text(
                "INSERT INTO serving.symbol_signal (symbol, move_id, version, "
                "move_version, move_window_end, status, headline_label, "
                "generation_method, updated_at, symbol_name) VALUES (:symbol, "
                ":move_id, :version, :move_version, :move_window_end, :status, "
                ":label, :method, :updated_at, :symbol_name)"
            ),
            current,
        )
        await session.execute(
            text(
                "INSERT INTO serving.symbol_signal_cause "
                "(symbol, rank, explanation, source_count) VALUES "
                f"('{SYMBOL}', 2, 'second', 1), ('{SYMBOL}', 1, 'first', 3)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO serving.symbol_signal_cause_source "
                "(symbol, rank, document_id) VALUES (:symbol, :rank, :doc)"
            ),
            [
                {"symbol": SYMBOL, "rank": rank, "doc": doc}
                for rank, doc in ((1, DOC_C), (1, DOC_A), (1, DOC_B), (2, DOC_D))
            ],
        )
        await session.execute(
            text(
                "INSERT INTO serving.symbol_signal_history_cause_source "
                "(move_id, version, rank, document_id) VALUES (:move_id, 2, :rank, :doc)"
            ),
            [
                {"move_id": move_day, "rank": rank, "doc": doc}
                for rank, doc in ((1, DOC_A), (1, DOC_B), (1, DOC_C), (2, DOC_D))
            ],
        )
    return move_day, move_next


async def _seed_snapshot(session_factory, change_rate: str) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO serving.symbol_snapshot (symbol, change_rate) "
                "VALUES (:symbol, :rate)"
            ),
            {"symbol": SYMBOL, "rate": Decimal(change_rate)},
        )


async def test_current_signal_returns_row_with_ranked_causes(signal_env):
    client, session_factory = signal_env
    move_day, _ = await _seed(session_factory)

    resp = await client.get(f"/api/stocks/{SYMBOL}/signal")

    assert resp.status_code == 200
    body = resp.json()
    assert body["move_id"] == str(move_day)
    assert (body["move_version"], body["version"]) == (2, 2)
    assert body["status"] == "SHOWN"
    assert body["headline_label"] == "AI 수익성 우려"
    assert body["generation_method"] == "llm"
    assert [c["rank"] for c in body["causes"]] == [1, 2]
    assert body["symbol_name"] == "삼성전자"
    assert body["headline"] is None
    assert body["causes"][0] == {
        "rank": 1,
        "explanation": "first",
        "source_count": 3,
        "source_document_ids": [str(DOC_A), str(DOC_B), str(DOC_C)],
    }
    assert body["causes"][1]["source_document_ids"] == [str(DOC_D)]


@pytest.mark.parametrize(
    ("change_rate", "headline"),
    [
        ("-3.25", "AI 수익성 우려로 삼성전자 3.3% 하락"),
        ("1.04", "AI 수익성 우려로 삼성전자 1.0% 상승"),
        ("0.04", "AI 수익성 우려에도 삼성전자 보합"),
    ],
)
async def test_current_signal_assembles_headline_from_live_change_rate(
    signal_env, change_rate, headline
):
    client, session_factory = signal_env
    await _seed(session_factory)
    await _seed_snapshot(session_factory, change_rate)

    body = (await client.get(f"/api/stocks/{SYMBOL}/signal")).json()

    assert body["headline_label"] == "AI 수익성 우려"
    assert body["headline"] == headline


async def test_current_signal_missing_is_404(signal_env):
    client, _ = signal_env

    resp = await client.get(f"/api/stocks/{SYMBOL}/signal")

    assert resp.status_code == 404


async def test_invalid_symbol_is_422(signal_env):
    client, _ = signal_env

    resp = await client.get("/api/stocks/abc/signal")

    assert resp.status_code == 422


async def test_history_filters_by_kst_date_and_orders_by_event_time(signal_env):
    client, session_factory = signal_env
    move_day, _ = await _seed(session_factory)

    resp = await client.get(
        f"/api/stocks/{SYMBOL}/signal/history", params={"date": DAY.isoformat()}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [(v["move_version"], v["version"]) for v in body] == [(1, 1), (2, 2)]
    assert {v["move_id"] for v in body} == {str(move_day)}
    assert body[0]["status"] == "HIDDEN"
    assert body[0]["causes"] == []
    assert [c["explanation"] for c in body[1]["causes"]] == ["first", "second"]
    assert body[1]["causes"][0]["source_document_ids"] == [
        str(DOC_A),
        str(DOC_B),
        str(DOC_C),
    ]
    assert body[0]["symbol_name"] == "삼성전자"
    assert all(v["headline"] is None for v in body)


async def test_history_requires_date_and_empty_day_is_empty_list(signal_env):
    client, _ = signal_env

    missing = await client.get(f"/api/stocks/{SYMBOL}/signal/history")
    empty = await client.get(
        f"/api/stocks/{SYMBOL}/signal/history", params={"date": "2026-01-02"}
    )

    assert missing.status_code == 422
    assert empty.status_code == 200
    assert empty.json() == []


async def test_query_functions_are_callable_without_http(signal_env):
    _, session_factory = signal_env
    _, move_next = await _seed(session_factory)

    current = await get_current_signal(session_factory, SYMBOL)
    next_day = await get_signal_history(session_factory, SYMBOL, DAY + timedelta(days=1))

    assert current is not None and current.status == "SHOWN"
    assert [v.move_id for v in next_day] == [move_next]
    assert next_day[0].status == "NO_CANDIDATE"

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from alert_service.api.app import create_app
from alert_service.db.session import create_engine, create_session_factory


pytestmark = pytest.mark.qa

_EVIDENCE_DIR = Path(__file__).resolve().parents[3] / ".sisyphus" / "evidence"

_DROP = "DROP SCHEMA IF EXISTS reference CASCADE"

_DDL = (
    "CREATE SCHEMA reference",
    """
    CREATE TABLE reference.bronze_consensus_report (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        report_idx TEXT NOT NULL,
        report_date DATE,
        stock_name TEXT,
        ticker TEXT,
        title TEXT,
        target_price INTEGER,
        investment_opinion TEXT,
        author TEXT,
        provider TEXT,
        full_text TEXT,
        company_info_url TEXT,
        attachment_url TEXT,
        attachment_filename TEXT,
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
async def consensus_env(postgres_container):
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


async def _seed_consensus(
    session_factory,
    *,
    ticker,
    report_date,
    provider,
    title,
    target_price,
    investment_opinion,
    author,
    full_text,
):
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO reference.bronze_consensus_report "
                "(report_idx, report_date, ticker, title, target_price, "
                "investment_opinion, author, provider, full_text) "
                "VALUES (:ridx, :rdate, :ticker, :title, :tp, :io, :author, :provider, :ft)"
            ),
            {
                "ridx": str(uuid.uuid4()),
                "rdate": report_date,
                "ticker": ticker,
                "title": title,
                "tp": target_price,
                "io": investment_opinion,
                "author": author,
                "provider": provider,
                "ft": full_text,
            },
        )
        await session.commit()


async def test_consensus_returns_meta_in_report_date_desc_without_fulltext(consensus_env):
    client, sf = consensus_env
    await _seed_consensus(
        sf,
        ticker="005930",
        report_date=date(2026, 5, 20),
        provider="Broker A",
        title="Buy on AI momentum",
        target_price=90000,
        investment_opinion="BUY",
        author="Analyst One",
        full_text="A" * 4000,
    )
    await _seed_consensus(
        sf,
        ticker="005930",
        report_date=date(2026, 6, 1),
        provider="Broker B",
        title="Raise target on HBM demand",
        target_price=110000,
        investment_opinion="STRONG BUY",
        author="Analyst Two",
        full_text="B" * 4000,
    )
    await _seed_consensus(
        sf,
        ticker="005930",
        report_date=date(2026, 4, 10),
        provider="Broker C",
        title="Hold ahead of guidance",
        target_price=82000,
        investment_opinion="HOLD",
        author="Analyst Three",
        full_text="C" * 4000,
    )

    resp = await client.get("/api/consensus/005930?limit=5")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3

    report_dates = [row["report_date"] for row in body]
    assert report_dates == ["2026-06-01", "2026-05-20", "2026-04-10"]
    assert report_dates == sorted(report_dates, reverse=True)

    assert body[0] == {
        "report_date": "2026-06-01",
        "provider": "Broker B",
        "title": "Raise target on HBM demand",
        "target_price": 110000,
        "investment_opinion": "STRONG BUY",
        "author": "Analyst Two",
    }

    expected_keys = {
        "report_date",
        "provider",
        "title",
        "target_price",
        "investment_opinion",
        "author",
    }
    for row in body:
        assert set(row.keys()) == expected_keys
        assert "full_text" not in row
        assert "summary" not in row

    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (_EVIDENCE_DIR / "task-4-consensus-happy.txt").write_text(
        "GET /api/consensus/005930?limit=5\n"
        f"status={resp.status_code}\n"
        f"len={len(body)}\n"
        f"report_date_order={report_dates}\n"
        f"response_keys={sorted(expected_keys)}\n"
        "full_text_present=False\n"
        "summary_present=False\n"
        f"body={body}\n"
    )


async def test_consensus_response_never_leaks_full_text_sentinel(consensus_env):
    client, sf = consensus_env
    sentinel = "FULLTEXT_SENTINEL_d41d8cd98f00b204e9800998ecf8427e"
    await _seed_consensus(
        sf,
        ticker="000660",
        report_date=date(2026, 5, 30),
        provider="Broker X",
        title="Memory cycle inflection",
        target_price=210000,
        investment_opinion="BUY",
        author="Analyst Z",
        full_text=f"Confidential body containing {sentinel} and more text.",
    )

    resp = await client.get("/api/consensus/000660")

    assert resp.status_code == 200
    raw = resp.text
    assert sentinel not in raw
    body = resp.json()
    assert len(body) == 1
    assert sentinel not in str(body)
    assert "full_text" not in body[0]
    assert "summary" not in body[0]

    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (_EVIDENCE_DIR / "task-4-consensus-no-fulltext.txt").write_text(
        "GET /api/consensus/000660\n"
        f"status={resp.status_code}\n"
        f"sentinel={sentinel}\n"
        f"sentinel_in_raw_response={sentinel in raw}\n"
        f"full_text_key_present={'full_text' in body[0]}\n"
        f"summary_key_present={'summary' in body[0]}\n"
        f"raw_response={raw}\n"
    )


async def test_consensus_unknown_symbol_returns_empty_list(consensus_env):
    client, _sf = consensus_env

    resp = await client.get("/api/consensus/999999")

    assert resp.status_code == 200
    assert resp.json() == []


async def test_consensus_respects_limit_and_desc_order(consensus_env):
    client, sf = consensus_env
    for day in range(1, 8):
        await _seed_consensus(
            sf,
            ticker="035720",
            report_date=date(2026, 6, day),
            provider=f"Broker {day}",
            title=f"Note {day}",
            target_price=10000 + day,
            investment_opinion="BUY",
            author=f"Analyst {day}",
            full_text="x" * 100,
        )

    resp = await client.get("/api/consensus/035720?limit=3")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert [row["report_date"] for row in body] == ["2026-06-07", "2026-06-06", "2026-06-05"]


async def test_consensus_limit_out_of_range_rejected(consensus_env):
    client, _sf = consensus_env

    too_big = await client.get("/api/consensus/005930?limit=51")
    too_small = await client.get("/api/consensus/005930?limit=0")

    assert too_big.status_code == 422
    assert too_small.status_code == 422

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false
"""Agent repository tests.

Live QA tests require:
ALERT_SERVICE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/invest_view
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from alert_service.agent.db_guard import guard
from alert_service.agent.repository import (
    _CONSENSUS_SQL,
    _FINANCIALS_SQL,
    _REPORT_BODY_SQL,
    _RECENT_REPORTS_SQL,
    _SEARCH_REPORTS_SQL,
    _SNAPSHOT_SQL,
    escape_like,
    fetch_consensus,
    fetch_financials,
    fetch_report_body,
    fetch_recent_reports,
    fetch_snapshot,
    search_reports_ilike,
)


def test_repository_sql_templates_pass_guard() -> None:
    for sql in [
        _FINANCIALS_SQL,
        _RECENT_REPORTS_SQL,
        _REPORT_BODY_SQL,
        _CONSENSUS_SQL,
        _SEARCH_REPORTS_SQL,
        _SNAPSHOT_SQL,
    ]:
        assert guard(sql) == sql


def test_escape_like_escapes_wildcards_and_escape_character() -> None:
    assert escape_like(r"100%_\done") == r"100\%\_\\done"


@pytest.fixture(scope="function")
async def live_session_factory():
    url = os.getenv("ALERT_SERVICE_DATABASE_URL")
    if not url:
        pytest.skip("ALERT_SERVICE_DATABASE_URL is unset; skipping live repository QA")

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external DB reachability
        await engine.dispose()
        pytest.skip(f"live DB unreachable: {exc}")

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.qa
async def test_fetch_financials_filters_items_and_coerces_values(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        rows = await fetch_financials(session, ["005930"], "INC", ["영업이익"], "Y")

    assert rows
    assert all(row["ticker"] == "005930" for row in rows)
    assert all(row["item"] == "영업이익" for row in rows)
    assert all(isinstance(row["value"], float) for row in rows)
    assert all(re.fullmatch(r"\d{4}-\d{2}", row["period"]) for row in rows)


@pytest.mark.qa
async def test_fetch_financials_period_range_and_empty_ticker(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        rows = await fetch_financials(
            session, ["005930"], "INC", ["*EBITDA"], "Y", "2018-12", "2025-12"
        )
        empty = await fetch_financials(session, ["999999"], "INC", None, "Y")

    assert rows
    assert all("2018-12" <= row["period"] <= "2025-12" for row in rows)
    assert empty == []


@pytest.mark.qa
async def test_fetch_recent_reports_metadata_only_sorted(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        rows = await fetch_recent_reports(session, ["005930"], 5)

    assert len(rows) <= 5
    report_dates = [row["report_date"] for row in rows]
    assert report_dates == sorted(report_dates, reverse=True)
    assert all("full_text" not in row and "summary" not in row for row in rows)
    assert all(isinstance(row["full_text_chars"], int) for row in rows)
    assert all(row["full_text_chars"] > 0 for row in rows)


@pytest.mark.qa
async def test_fetch_report_body_returns_bounded_body_and_missing_none(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        recent = await fetch_recent_reports(session, ["005930"], 1)
        assert recent
        report = await fetch_report_body(session, ["005930"], recent[0]["report_idx"], 4000)
        missing = await fetch_report_body(session, ["005930"], 999999999, 4000)

    assert report is not None
    assert report["report_idx"] == recent[0]["report_idx"]
    assert isinstance(report["body_text"], str)
    assert len(report["body_text"]) > 0
    assert len(report["body_text"]) <= 4000
    assert isinstance(report["full_text_chars"], int)
    assert report["full_text_chars"] > 0
    assert isinstance(report["truncated"], bool)
    assert missing is None


@pytest.mark.qa
async def test_fetch_consensus_aggregates_by_provider(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        rows = await fetch_consensus(session, ["005930"])

    assert rows
    assert all(row["ticker"] == "005930" for row in rows)
    assert all(isinstance(row["avg_target_price"], float) for row in rows)


@pytest.mark.qa
async def test_search_reports_ilike_filters_and_neutralizes_wildcards(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        rows = await search_reports_ilike(session, ["005930"], "반도체", 5)
        injection_rows = await search_reports_ilike(session, ["005930"], "100%' OR '1'='1", 5)

    assert all(row["ticker"] == "005930" for row in rows)
    assert all(isinstance(row["body_snippet"], str) for row in rows)
    assert all(row["body_snippet"] for row in rows)
    assert isinstance(injection_rows, list)


@pytest.mark.qa
async def test_fetch_snapshot_returns_serving_snapshot(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with live_session_factory() as session:
        rows = await fetch_snapshot(session, ["005930"])

    assert isinstance(rows, list)
    assert all(row["symbol"] == "005930" for row in rows)

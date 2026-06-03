# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false
"""Tests for the ``search_reports`` Strands tool (Task T9).

Structural (non-qa) tests verify the tool is importable, decorated, and exposes
only ``keyword``/``limit`` (ambient ``current_ticker`` is injected via
``ToolContext.invocation_state``, never as an LLM-visible parameter).

Live QA tests (skipped without a reachable DB) exercise ``_search_impl`` against
real data:
    ALERT_SERVICE_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/invest_view
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from alert_service.agent.tools.search import _search_impl, search_reports


def test_search_reports_is_decorated_strands_tool() -> None:
    assert search_reports.tool_name == "search_reports"
    assert isinstance(search_reports.tool_spec, dict)
    assert inspect.iscoroutinefunction(_search_impl)


def test_search_reports_exposes_keyword_and_limit_only_no_ticker() -> None:
    props = search_reports.tool_spec["inputSchema"]["json"]["properties"]
    assert "keyword" in props
    assert "limit" in props
    assert "ticker" not in props
    assert "tickers" not in props
    assert "tool_context" not in props
    assert search_reports.tool_spec["inputSchema"]["json"]["required"] == ["keyword"]


def test_search_reports_docstring_is_korean_ilike_description() -> None:
    description = search_reports.tool_spec["description"]
    assert "ILIKE" in description
    assert "키워드" in description


@pytest.fixture(scope="function")
async def live_session_factory():
    url = os.getenv("ALERT_SERVICE_DATABASE_URL")
    if not url:
        pytest.skip("ALERT_SERVICE_DATABASE_URL is unset; skipping live search QA")

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
async def test_search_impl_scopes_results_to_current_ticker(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    rows = await _search_impl(live_session_factory, "005930", "반도체", 5)

    assert isinstance(rows, list)
    assert len(rows) <= 5
    assert all(row["ticker"] == "005930" for row in rows)


@pytest.mark.qa
async def test_search_impl_neutralizes_injection_payload(
    live_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    injection_rows = await _search_impl(
        live_session_factory, "005930", "100%' OR '1'='1", 5
    )

    assert isinstance(injection_rows, list)
    assert all(row["ticker"] == "005930" for row in injection_rows)

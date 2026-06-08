from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false, reportExplicitAny=false, reportUnknownParameterType=false, reportMissingParameterType=false

import inspect
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from alert_service.agent.tools.reports import (
    _consensus_impl,
    _report_body_impl,
    _recent_reports_impl,
    get_consensus,
    get_recent_reports,
    get_report_body,
)


def test_report_tools_are_importable_strands_tools_with_expected_signatures() -> None:
    assert hasattr(get_recent_reports, "tool_spec")
    assert hasattr(get_consensus, "tool_spec")
    assert hasattr(get_report_body, "tool_spec")
    assert get_recent_reports.tool_spec["name"] == "get_recent_reports"
    assert get_consensus.tool_spec["name"] == "get_consensus"
    assert get_report_body.tool_spec["name"] == "get_report_body"

    recent_params = inspect.signature(get_recent_reports).parameters
    consensus_params = inspect.signature(get_consensus).parameters
    body_params = inspect.signature(get_report_body).parameters
    recent_schema = get_recent_reports.tool_spec["inputSchema"]["json"]["properties"]
    consensus_schema = get_consensus.tool_spec["inputSchema"]["json"]["properties"]
    body_schema = get_report_body.tool_spec["inputSchema"]["json"]["properties"]

    assert "limit" in recent_params
    assert "limit" in recent_schema
    assert "ticker" not in recent_params
    assert "ticker" not in recent_schema

    assert "ticker" not in consensus_params
    assert "ticker" not in consensus_schema

    assert "report_idx" in body_params
    assert "max_chars" in body_params
    assert "report_idx" in body_schema
    assert "max_chars" in body_schema
    assert "ticker" not in body_params
    assert "ticker" not in body_schema


@pytest.fixture(scope="function")
async def live_session_factory():
    url = os.getenv("ALERT_SERVICE_DATABASE_URL")
    if not url:
        pytest.skip("ALERT_SERVICE_DATABASE_URL is unset; skipping live report tool QA")

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
async def test_recent_reports_impl_metadata_only_sorted(live_session_factory) -> None:
    rows = await _recent_reports_impl(live_session_factory, "005930", 5)

    assert len(rows) <= 5
    assert all(row["ticker"] == "005930" for row in rows)
    report_dates = [row["report_date"] for row in rows]
    assert report_dates == sorted(report_dates, reverse=True)
    assert all("summary" not in row and "full_text" not in row for row in rows)
    assert all(row["full_text_chars"] > 0 for row in rows)


@pytest.mark.qa
async def test_consensus_impl_aggregates_with_float_target_price(live_session_factory) -> None:
    rows = await _consensus_impl(live_session_factory, "005930")

    assert rows
    assert all(row["ticker"] == "005930" for row in rows)
    assert all(isinstance(row["avg_target_price"], float) for row in rows)


@pytest.mark.qa
async def test_report_body_impl_returns_body_for_existing_report(live_session_factory) -> None:
    reports = await _recent_reports_impl(live_session_factory, "005930", 1)
    assert reports

    body = await _report_body_impl(
        live_session_factory, "005930", reports[0]["report_idx"], 4000
    )

    assert body["report_idx"] == reports[0]["report_idx"]
    assert isinstance(body["body_text"], str)
    assert len(body["body_text"]) > 0
    assert len(body["body_text"]) <= 4000
    assert body["full_text_chars"] > 0

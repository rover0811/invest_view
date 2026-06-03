from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false, reportExplicitAny=false, reportUnknownParameterType=false, reportMissingParameterType=false

import inspect
import os
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alert_service.agent.tools.financial import (
    _compare_financials_impl,
    _get_financials_impl,
    compare_financials,
    get_financials,
)


@dataclass(frozen=True)
class FakeToolContext:
    invocation_state: dict[str, Any]


def test_financial_tools_are_importable_strands_tools_with_expected_signatures() -> None:
    assert hasattr(get_financials, "tool_spec")
    assert hasattr(compare_financials, "tool_spec")
    assert get_financials.tool_spec["name"] == "get_financials"
    assert compare_financials.tool_spec["name"] == "compare_financials"

    get_params = inspect.signature(get_financials).parameters
    compare_params = inspect.signature(compare_financials).parameters

    assert "ticker" not in get_params
    assert "tickers" not in get_financials.tool_spec["inputSchema"]["json"]["properties"]
    assert "tickers" in compare_params
    assert "tickers" in compare_financials.tool_spec["inputSchema"]["json"]["properties"]


@pytest.fixture(scope="function")
async def live_session_factory():
    url = os.getenv("ALERT_SERVICE_DATABASE_URL")
    if not url:
        pytest.skip("ALERT_SERVICE_DATABASE_URL is unset; skipping live financial tool QA")

    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - depends on external DB reachability
        await engine.dispose()
        pytest.skip(f"live DB unreachable: {exc}")

    from sqlalchemy.ext.asyncio import async_sessionmaker

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.qa
async def test_get_financials_tool_impl_uses_ambient_ticker(live_session_factory) -> None:
    context = FakeToolContext(
        invocation_state={
            "current_ticker": "005930",
            "session_factory": live_session_factory,
        }
    )

    rows = await _get_financials_impl(context, "INC", ["영업이익"])

    assert rows
    assert all(row["ticker"] == "005930" for row in rows)
    assert any(row["item"] == "영업이익" for row in rows)


@pytest.mark.qa
async def test_compare_financials_tool_impl_uses_explicit_tickers(live_session_factory) -> None:
    context = FakeToolContext(invocation_state={"session_factory": live_session_factory})

    rows = await _compare_financials_impl(
        context,
        ["005930", "000660"],
        "INC",
        ["*EBITDA"],
    )

    assert rows
    assert {"005930", "000660"}.issubset({row["ticker"] for row in rows})
    assert all(row["item"] == "*EBITDA" for row in rows)

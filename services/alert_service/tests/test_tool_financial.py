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


class FakeSession:
    pass


class FakeSessionContextManager:
    session: FakeSession

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeSessionFactory:
    session: FakeSession

    def __init__(self) -> None:
        self.session = FakeSession()

    def __call__(self) -> FakeSessionContextManager:
        return FakeSessionContextManager(self.session)


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


async def test_financial_tools_resolve_friendly_item_names_before_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = FakeSessionFactory()
    context = FakeToolContext(
        invocation_state={"current_ticker": "005930", "session_factory": session_factory}
    )
    calls: list[tuple[object, list[str], str, list[str] | None, str, str | None, str | None]] = []

    async def fake_fetch_financials(
        session: object,
        tickers: list[str],
        stmt_type: str,
        item_names: list[str] | None = None,
        period_type: str = "Y",
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> list[dict[str, object]]:
        calls.append((session, tickers, stmt_type, item_names, period_type, start_period, end_period))
        return [{"ticker": tickers[0], "period": "2024-12", "item": (item_names or [""])[0], "value": 1.0, "unit": "천원"}]

    monkeypatch.setattr("alert_service.agent.tools.financial.fetch_financials", fake_fetch_financials)

    await _get_financials_impl(context, "INC", ["주당순이익", "매출액"])
    await _compare_financials_impl(context, ["005930", "000660"], "INC", ["eps"])

    assert calls == [
        (session_factory.session, ["005930"], "INC", ["*주당순이익", "매출액(수익)"], "Y", None, None),
        (session_factory.session, ["005930", "000660"], "INC", ["*주당순이익"], "Y", None, None),
    ]


async def test_get_financials_empty_result_returns_available_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = FakeSessionFactory()
    context = FakeToolContext(
        invocation_state={"current_ticker": "005930", "session_factory": session_factory}
    )
    search_calls: list[tuple[object, list[str], str | None, str | None, int]] = []

    async def fake_fetch_financials(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    async def fake_search_financial_items(
        session: object,
        tickers: list[str],
        stmt_type: str | None = None,
        keyword: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        search_calls.append((session, tickers, stmt_type, keyword, limit))
        return [
            {"stmt_type": "INC", "item_name": "판매비와관리비", "unit": "천원", "periods": 10, "latest_period": "2025-12"},
            {"stmt_type": "INC", "item_name": "기타판매비와관리비", "unit": "천원", "periods": 8, "latest_period": "2025-12"},
        ]

    monkeypatch.setattr("alert_service.agent.tools.financial.fetch_financials", fake_fetch_financials)
    monkeypatch.setattr("alert_service.agent.tools.financial.search_financial_items", fake_search_financial_items)

    result = await _get_financials_impl(context, "INC", ["판매관리비"])

    assert search_calls == [(session_factory.session, ["005930"], "INC", "판매관리비", 15)]
    assert result == {
        "status": "no_data",
        "requested": ["판매관리비"],
        "available_matches": ["판매비와관리비", "기타판매비와관리비"],
    }


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

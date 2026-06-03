from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false, reportExplicitAny=false, reportUnknownParameterType=false, reportMissingParameterType=false

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from alert_service.agent import market_analyst
from alert_service.agent.tools.chart import _render_chart_impl, render_chart


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


def _context(*, chart_sink: asyncio.Queue[dict[str, object]] | None = None) -> FakeToolContext:
    invocation_state: dict[str, Any] = {
        "current_ticker": "005930",
        "session_factory": FakeSessionFactory(),
    }
    if chart_sink is not None:
        invocation_state["chart_sink"] = chart_sink
    return FakeToolContext(invocation_state=invocation_state)


def test_render_chart_is_strands_tool_with_expected_schema() -> None:
    assert hasattr(render_chart, "tool_spec")
    assert hasattr(render_chart, "_tool_func")
    assert render_chart.tool_name == "render_chart"
    assert render_chart.tool_spec["name"] == "render_chart"

    params = inspect.signature(render_chart).parameters
    properties = render_chart.tool_spec["inputSchema"]["json"]["properties"]

    assert "tool_context" not in properties
    assert "ticker" not in params
    assert "ticker" not in properties
    assert set(properties) == {
        "item_names",
        "chart_type",
        "stmt_type",
        "start_period",
        "end_period",
    }


async def test_render_chart_builds_spec_pushes_queue_and_returns_small_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart_sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    context = _context(chart_sink=chart_sink)
    calls: list[tuple[object, list[str], str, list[str], str, str | None, str | None]] = []

    async def fake_fetch_financials(
        session: object,
        tickers: list[str],
        stmt_type: str,
        item_names: list[str] | None = None,
        period_type: str = "Y",
        start_period: str | None = None,
        end_period: str | None = None,
    ) -> list[dict[str, object]]:
        calls.append((session, tickers, stmt_type, item_names or [], period_type, start_period, end_period))
        return [
            {"ticker": "005930", "period": "2024-12", "item": "영업이익", "value": 57000000.0, "unit": "천원"},
            {"ticker": "005930", "period": "2022-12", "item": "영업이익", "value": 43000000, "unit": "천원"},
            {"ticker": "005930", "period": "2023-12", "item": "영업이익", "value": None, "unit": "천원"},
            {"ticker": "005930", "period": "2024-12", "item": "매출액(수익)", "value": 301000000.0, "unit": "천원"},
            {"ticker": "005930", "period": "2022-12", "item": "매출액(수익)", "value": 279000000.0, "unit": "천원"},
        ]

    monkeypatch.setattr("alert_service.agent.tools.chart.fetch_financials", fake_fetch_financials)

    result = await _render_chart_impl(
        context,
        ["매출액", "영업이익"],
        "pie",
        "INC",
        "2022-12",
        "2024-12",
    )

    assert calls == [
        (
            context.invocation_state["session_factory"].session,  # pyright: ignore[reportAny]
            ["005930"],
            "INC",
            ["매출액(수익)", "영업이익"],
            "Y",
            "2022-12",
            "2024-12",
        )
    ]
    assert result == {
        "status": "success",
        "chart_type": "line",
        "items": ["매출액(수익)", "영업이익"],
        "periods": 2,
    }
    assert "series" not in result
    assert "points" not in result

    spec = chart_sink.get_nowait()
    assert chart_sink.empty()
    assert set(spec) == {"chart_type", "title", "x_label", "y_label", "unit", "series"}
    assert spec["chart_type"] == "line"
    assert spec["title"] == "005930 재무 추이"
    assert spec["x_label"] == "기간"
    assert spec["y_label"] == "천원"
    assert spec["unit"] == "천원"
    assert spec["series"] == [
        {
            "name": "매출액(수익)",
            "points": [
                {"x": "2022-12", "y": 279000000.0},
                {"x": "2024-12", "y": 301000000.0},
            ],
        },
        {
            "name": "영업이익",
            "points": [
                {"x": "2022-12", "y": 43000000.0},
                {"x": "2024-12", "y": 57000000.0},
            ],
        },
    ]


async def test_render_chart_bar_type_and_decorated_tool_func(monkeypatch: pytest.MonkeyPatch) -> None:
    chart_sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    async def fake_fetch_financials(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {"ticker": "005930", "period": "2023-12", "item": "영업이익", "value": 1, "unit": "천원"},
        ]

    monkeypatch.setattr("alert_service.agent.tools.chart.fetch_financials", fake_fetch_financials)

    result = await render_chart._tool_func(_context(chart_sink=chart_sink), ["영업이익"], "bar")

    spec = chart_sink.get_nowait()
    assert result["chart_type"] == "bar"
    assert spec["chart_type"] == "bar"


async def test_render_chart_empty_data_returns_no_data_and_does_not_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart_sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()

    async def fake_fetch_financials(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {"ticker": "005930", "period": "2024-12", "item": "영업이익", "value": None, "unit": "천원"},
        ]

    monkeypatch.setattr("alert_service.agent.tools.chart.fetch_financials", fake_fetch_financials)

    async def fake_search_financial_items(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr("alert_service.agent.tools.chart.search_financial_items", fake_search_financial_items)

    result = await _render_chart_impl(_context(chart_sink=chart_sink), ["영업이익"])

    assert result == {"status": "no_data", "items": ["영업이익"], "available_matches": []}
    assert chart_sink.empty()


async def test_render_chart_empty_data_includes_available_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart_sink: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    calls: list[tuple[object, list[str], str | None, str | None, int]] = []

    async def fake_fetch_financials(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    async def fake_search_financial_items(
        session: object,
        tickers: list[str],
        stmt_type: str | None = None,
        keyword: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        calls.append((session, tickers, stmt_type, keyword, limit))
        return [
            {"stmt_type": "BAL", "item_name": "자산총계", "unit": "천원", "periods": 10, "latest_period": "2025-12"},
            {"stmt_type": "BAL", "item_name": "부채총계", "unit": "천원", "periods": 10, "latest_period": "2025-12"},
        ]

    monkeypatch.setattr("alert_service.agent.tools.chart.fetch_financials", fake_fetch_financials)
    monkeypatch.setattr("alert_service.agent.tools.chart.search_financial_items", fake_search_financial_items)

    context = _context(chart_sink=chart_sink)
    result = await _render_chart_impl(context, ["없는항목"], stmt_type="BAL")

    assert calls == [
        (context.invocation_state["session_factory"].session, ["005930"], "BAL", None, 15)  # pyright: ignore[reportAny]
    ]
    assert result == {
        "status": "no_data",
        "items": ["없는항목"],
        "available_matches": ["자산총계", "부채총계"],
    }
    assert chart_sink.empty()


async def test_render_chart_missing_chart_sink_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_financials(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return [
            {"ticker": "005930", "period": "2024-12", "item": "영업이익", "value": 57000000.0, "unit": "천원"},
        ]

    monkeypatch.setattr("alert_service.agent.tools.chart.fetch_financials", fake_fetch_financials)

    result = await _render_chart_impl(_context(), ["영업이익"])

    assert result == {
        "status": "success",
        "chart_type": "line",
        "items": ["영업이익"],
        "periods": 1,
    }


def test_render_chart_registered_in_market_analyst_tools() -> None:
    assert render_chart in market_analyst.AGENT_TOOLS
    assert [tool.tool_name for tool in market_analyst.AGENT_TOOLS] == [
        "get_symbol_snapshot",
        "get_financials",
        "compare_financials",
        "search_financial_items",
        "render_chart",
        "get_recent_reports",
        "get_report_body",
        "get_consensus",
        "search_reports",
    ]

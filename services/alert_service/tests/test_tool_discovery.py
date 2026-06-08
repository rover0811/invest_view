from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false, reportExplicitAny=false, reportUnknownParameterType=false, reportMissingParameterType=false

import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from alert_service.agent.tools.discovery import _search_financial_items_impl, search_financial_items


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


def test_search_financial_items_is_strands_tool_with_expected_schema() -> None:
    assert hasattr(search_financial_items, "tool_spec")
    assert search_financial_items.tool_name == "search_financial_items"

    params = inspect.signature(search_financial_items).parameters
    properties = search_financial_items.tool_spec["inputSchema"]["json"]["properties"]

    assert "tool_context" not in properties
    assert "ticker" not in params
    assert "ticker" not in properties
    assert set(properties) == {"keyword", "stmt_type"}


async def test_search_financial_items_impl_uses_ambient_ticker_and_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = FakeSessionFactory()
    calls: list[tuple[object, list[str], str | None, str | None, int]] = []

    async def fake_search_financial_items(
        session: object,
        tickers: list[str],
        stmt_type: str | None = None,
        keyword: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, object]]:
        calls.append((session, tickers, stmt_type, keyword, limit))
        return [
            {
                "stmt_type": "INC",
                "item_name": "판매비와관리비",
                "unit": "천원",
                "periods": 10,
                "latest_period": "2025-12",
            }
        ]

    monkeypatch.setattr(
        "alert_service.agent.tools.discovery.repository_search_financial_items",
        fake_search_financial_items,
    )

    rows = await _search_financial_items_impl(session_factory, "005930", "판매", "INC")

    assert calls == [(session_factory.session, ["005930"], "INC", "판매", 30)]
    assert rows == [
        {
            "stmt_type": "INC",
            "item_name": "판매비와관리비",
            "unit": "천원",
            "periods": 10,
            "latest_period": "2025-12",
        }
    ]


async def test_search_financial_items_tool_requires_ambient_context() -> None:
    context = FakeToolContext(invocation_state={})

    with pytest.raises(ValueError, match="session_factory"):
        await search_financial_items._tool_func(context)

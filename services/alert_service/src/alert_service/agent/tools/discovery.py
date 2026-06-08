from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from strands import ToolContext, tool
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.repository import search_financial_items as repository_search_financial_items


_SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class _ToolContextLike(Protocol):
    invocation_state: Mapping[str, object]


def _require_session_factory(tool_context: _ToolContextLike) -> _SessionFactory:
    session_factory = tool_context.invocation_state.get("session_factory")
    if not callable(session_factory):
        raise ValueError("session_factory is required in invocation_state")
    return cast(_SessionFactory, session_factory)


def _require_current_ticker(tool_context: _ToolContextLike) -> str:
    ticker = tool_context.invocation_state.get("current_ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("current_ticker is required in invocation_state")
    return ticker


async def _search_financial_items_impl(
    session_factory: _SessionFactory,
    ticker: str,
    keyword: str | None = None,
    stmt_type: str | None = None,
    limit: int = 30,
) -> list[dict[str, object]]:
    async with session_factory() as session:
        return await repository_search_financial_items(
            session,
            [ticker],
            stmt_type=stmt_type,
            keyword=keyword,
            limit=limit,
        )


@tool(context=True)
async def search_financial_items(
    tool_context: ToolContext,
    keyword: str | None = None,
    stmt_type: str | None = None,
) -> list[dict[str, object]]:
    """현재 종목의 재무제표에 실제 존재하는 항목명을 검색한다. 정확한 item_name을 모를 때 먼저 호출해 실제 항목명을 확인한 뒤 get_financials/render_chart에 그 이름을 사용한다. keyword(부분일치, 예 '주당순이익','현금','부채'), stmt_type(INC/BAL/CAS) 필터 가능. 연간만."""
    return await _search_financial_items_impl(
        _require_session_factory(tool_context),
        _require_current_ticker(tool_context),
        keyword,
        stmt_type,
    )

from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from strands import ToolContext, tool
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.repository import fetch_financials


_SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class _ToolContextLike(Protocol):
    invocation_state: Mapping[str, object]


def _require_session_factory(tool_context: _ToolContextLike) -> _SessionFactory:
    session_factory = tool_context.invocation_state.get("session_factory")
    if not callable(session_factory):
        raise ValueError("session_factory is required in invocation_state")
    return cast(_SessionFactory, session_factory)


async def _get_financials_impl(
    tool_context: _ToolContextLike,
    stmt_type: str,
    item_names: list[str] | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
) -> list[dict[str, object]]:
    ticker = tool_context.invocation_state.get("current_ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("current_ticker is required in invocation_state")

    session_factory = _require_session_factory(tool_context)
    async with session_factory() as session:
        return await fetch_financials(
            session,
            [ticker],
            stmt_type,
            item_names,
            "Y",
            start_period,
            end_period,
        )


async def _compare_financials_impl(
    tool_context: _ToolContextLike,
    tickers: list[str],
    stmt_type: str,
    item_names: list[str] | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
) -> list[dict[str, object]]:
    session_factory = _require_session_factory(tool_context)
    async with session_factory() as session:
        return await fetch_financials(
            session,
            tickers,
            stmt_type,
            item_names,
            "Y",
            start_period,
            end_period,
        )


@tool(context=True)
async def get_financials(
    tool_context: ToolContext,
    stmt_type: str,
    item_names: list[str] | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
) -> list[dict[str, object]]:
    """현재 종목 재무제표 수치 추출. stmt_type BAL/INC/CAS, item_names는 한국어 항목명. 연간(Y) 데이터만 제공."""
    return await _get_financials_impl(
        tool_context,
        stmt_type,
        item_names,
        start_period,
        end_period,
    )


@tool(context=True)
async def compare_financials(
    tool_context: ToolContext,
    tickers: list[str],
    stmt_type: str,
    item_names: list[str] | None = None,
    start_period: str | None = None,
    end_period: str | None = None,
) -> list[dict[str, object]]:
    """다종목 재무 비교. tickers를 명시적으로 받아 같은 항목과 기간을 비교. 연간(Y) 데이터만 제공."""
    return await _compare_financials_impl(
        tool_context,
        tickers,
        stmt_type,
        item_names,
        start_period,
        end_period,
    )

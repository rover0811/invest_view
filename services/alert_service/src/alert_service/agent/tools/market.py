from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from strands import ToolContext, tool
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.repository import fetch_snapshot


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


def _price_realtime_ttl_seconds(tool_context: _ToolContextLike) -> int:
    value = tool_context.invocation_state.get("price_realtime_ttl_seconds")
    if isinstance(value, int):
        return value
    if isinstance(value, str | float):
        return int(value)
    return 300


async def _snapshot_impl(
    session_factory: _SessionFactory, ticker: str, ttl_seconds: int = 300
) -> dict[str, object]:
    async with session_factory() as session:
        rows = await fetch_snapshot(session, [ticker], ttl_seconds=ttl_seconds)
    return rows[0] if rows else {}


@tool(context=True)
async def get_symbol_snapshot(tool_context: ToolContext) -> dict[str, object]:
    """현재 종목의 최신 시세 스냅샷. 종목 질문 시 가장 먼저 호출."""
    ticker = _require_current_ticker(tool_context)
    session_factory = _require_session_factory(tool_context)
    return await _snapshot_impl(session_factory, ticker, _price_realtime_ttl_seconds(tool_context))

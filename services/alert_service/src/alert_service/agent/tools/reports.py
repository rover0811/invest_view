from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from strands import ToolContext, tool
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.repository import (
    fetch_consensus,
    fetch_recent_reports,
    fetch_report_body,
)


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


async def _recent_reports_impl(
    session_factory: _SessionFactory, ticker: str, limit: int = 5
) -> list[dict[str, object]]:
    async with session_factory() as session:
        return await fetch_recent_reports(session, [ticker], limit)


async def _consensus_impl(
    session_factory: _SessionFactory, ticker: str
) -> list[dict[str, object]]:
    async with session_factory() as session:
        return await fetch_consensus(session, [ticker])


async def _report_body_impl(
    session_factory: _SessionFactory, ticker: str, report_idx: int, max_chars: int = 4000
) -> dict[str, object]:
    async with session_factory() as session:
        report = await fetch_report_body(session, [ticker], report_idx, max_chars)
    return report or {}


@tool(context=True)
async def get_recent_reports(
    tool_context: ToolContext, limit: int = 5
) -> list[dict[str, object]]:
    """현재 종목의 최근 리포트 N건 (날짜순, 검색 아님)."""
    return await _recent_reports_impl(
        _require_session_factory(tool_context),
        _require_current_ticker(tool_context),
        limit,
    )


@tool(context=True)
async def get_consensus(tool_context: ToolContext) -> list[dict[str, object]]:
    """현재 종목 목표주가/투자의견 컨센서스 집계 (provider별 평균)."""
    return await _consensus_impl(
        _require_session_factory(tool_context),
        _require_current_ticker(tool_context),
    )


@tool(context=True)
async def get_report_body(
    tool_context: ToolContext, report_idx: int, max_chars: int = 4000
) -> dict[str, object]:
    """특정 리포트(report_idx)의 본문 내용을 가져온다. 리포트 분석·요약 시 사용. max_chars로 길이 제한(기본 4000)."""
    return await _report_body_impl(
        _require_session_factory(tool_context),
        _require_current_ticker(tool_context),
        report_idx,
        max_chars,
    )

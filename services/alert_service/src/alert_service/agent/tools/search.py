"""Strands ``search_reports`` tool: ticker-scoped ILIKE over report ``full_text``.

SQL and injection-safe escaping are owned by ``repository.search_reports_ilike``;
this module only wires ambient ``current_ticker``/``session_factory`` from the
Strands ``ToolContext`` into it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strands import ToolContext, tool

from alert_service.agent.repository import search_reports_ilike


async def _search_impl(
    session_factory: async_sessionmaker[AsyncSession],
    ticker: str,
    keyword: str,
    limit: int = 5,
) -> list[dict[str, object]]:
    async with session_factory() as session:
        return await search_reports_ilike(session, [ticker], keyword, limit)


@tool(context=True)
async def search_reports(
    tool_context: ToolContext, keyword: str, limit: int = 5
) -> list[dict[str, object]]:
    """현재 종목 리포트 본문에서 키워드 검색 (ticker 스코프 ILIKE)."""
    state = tool_context.invocation_state
    return await _search_impl(
        state["session_factory"], state["current_ticker"], keyword, limit
    )

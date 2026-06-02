"""Public read-only consensus report API over ``reference.bronze_consensus_report``.

Title + metadata only: ``full_text`` is intentionally NEVER selected or returned
(it is a large raw report body, out of scope for this listing). Results are ordered
by ``report_date`` descending; there is no full-text search here.

``report_date`` is a DATE column and is serialized as an ISO ``YYYY-MM-DD`` string.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


router = APIRouter(prefix="/api", tags=["consensus"])


_CONSENSUS_SQL = text(
    """
    SELECT report_date, provider, title, target_price, investment_opinion, author
    FROM reference.bronze_consensus_report
    WHERE ticker = :symbol
    ORDER BY report_date DESC NULLS LAST
    LIMIT :limit
    """
)


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


@router.get("/consensus/{symbol}")
async def get_consensus(
    request: Request,
    symbol: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> list[dict[str, Any]]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        result = await session.execute(_CONSENSUS_SQL, {"symbol": symbol, "limit": limit})
        rows = result.all()
    return [
        {
            "report_date": str(row.report_date) if row.report_date is not None else None,
            "provider": row.provider,
            "title": row.title,
            "target_price": row.target_price,
            "investment_opinion": row.investment_opinion,
            "author": row.author,
        }
        for row in rows
    ]

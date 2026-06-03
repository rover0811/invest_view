"""Read-only raw-SQL repository functions for agent tools."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import text
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.db_guard import guard


_FINANCIALS_SQL = """
SELECT ticker, period, item_name AS item, value, unit
FROM reference.financial_metrics
WHERE ticker = ANY(CAST(:tickers AS text[]))
  AND stmt_type = :stmt_type
  AND period_type = :period_type
  AND (CAST(:item_names AS text[]) IS NULL OR item_name = ANY(CAST(:item_names AS text[])))
  AND (CAST(:start_period AS text) IS NULL OR period >= CAST(:start_period AS text))
  AND (CAST(:end_period AS text) IS NULL OR period <= CAST(:end_period AS text))
ORDER BY ticker, period DESC
"""

_SEARCH_FINANCIAL_ITEMS_SQL = r"""
SELECT stmt_type, item_name, unit,
       count(DISTINCT period) AS periods, max(period) AS latest_period
FROM reference.financial_metrics
WHERE ticker = ANY(CAST(:tickers AS text[]))
  AND period_type = 'Y'
  AND (CAST(:stmt_type AS text) IS NULL OR stmt_type = :stmt_type)
  AND (CAST(:keyword AS text) IS NULL OR item_name ILIKE :pattern ESCAPE '\')
GROUP BY stmt_type, item_name, unit
ORDER BY periods DESC, item_name
LIMIT :limit
"""

_RECENT_REPORTS_SQL = """
SELECT report_idx, report_date, ticker, title, target_price, investment_opinion, author, provider,
       char_length(full_text) AS full_text_chars
FROM reference.bronze_consensus_report
WHERE ticker = ANY(CAST(:tickers AS text[]))
ORDER BY report_date DESC
LIMIT :limit
"""

_REPORT_BODY_SQL = """
SELECT report_idx, title, provider, author, report_date, target_price, investment_opinion,
       char_length(full_text) AS full_text_chars,
       substring(full_text FROM (:offset + 1) FOR :max_chars) AS body_text,
       (char_length(full_text) > (:offset + :max_chars)) AS truncated
FROM reference.bronze_consensus_report
WHERE ticker = ANY(CAST(:tickers AS text[])) AND report_idx = :report_idx
"""

_CONSENSUS_SQL = """
SELECT ticker, provider, AVG(target_price)::float AS avg_target_price, COUNT(*) AS n, MAX(report_date) AS latest
FROM reference.bronze_consensus_report
WHERE ticker = ANY(CAST(:tickers AS text[]))
  AND target_price IS NOT NULL
GROUP BY ticker, provider
ORDER BY ticker, provider
"""

_SEARCH_REPORTS_SQL = r"""
SELECT report_idx, report_date, ticker, title, provider,
       substring(full_text FROM greatest(1, position(lower(:keyword) in lower(full_text)) - 500) FOR 1200) AS body_snippet
FROM reference.bronze_consensus_report
WHERE ticker = ANY(CAST(:tickers AS text[]))
  AND full_text ILIKE :pattern ESCAPE '\'
ORDER BY report_date DESC
LIMIT :limit
"""

_SNAPSHOT_SQL = """
SELECT symbol, last_price, change, change_rate, change_sign, cumulative_volume,
       trade_strength, vi_trigger_price, trading_halted, last_trade_time, updated_at
FROM serving.symbol_snapshot
WHERE symbol = ANY(CAST(:tickers AS text[]))
"""


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str | int | float | Decimal):
        return float(value)
    raise TypeError(f"Cannot coerce {type(value).__name__} to float")


def _to_isoformat(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)


def _mappings(result: Result[tuple[object, ...]]) -> list[Mapping[str, object]]:
    return cast(list[Mapping[str, object]], result.mappings().all())


def escape_like(keyword: str) -> str:
    """Escape LIKE wildcards and the escape character for parameterized ILIKE."""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def fetch_financials(
    session: AsyncSession,
    tickers: list[str],
    stmt_type: str,
    item_names: list[str] | None = None,
    period_type: str = "Y",
    start_period: str | None = None,
    end_period: str | None = None,
) -> list[dict[str, object]]:
    result = await session.execute(
        text(guard(_FINANCIALS_SQL)),
        {
            "tickers": tickers,
            "stmt_type": stmt_type,
            "period_type": period_type,
            "item_names": item_names,
            "start_period": start_period,
            "end_period": end_period,
        },
    )
    rows = _mappings(result)
    return [
        {
            "ticker": row["ticker"],
            "period": row["period"],
            "item": row["item"],
            "value": _to_float(row["value"]),
            "unit": row["unit"],
        }
        for row in rows
    ]


async def search_financial_items(
    session: AsyncSession,
    tickers: list[str],
    stmt_type: str | None = None,
    keyword: str | None = None,
    limit: int = 30,
) -> list[dict[str, object]]:
    normalized_stmt_type = stmt_type.strip().upper() if stmt_type and stmt_type.strip() else None
    normalized_keyword = keyword.strip() if keyword and keyword.strip() else None
    result = await session.execute(
        text(guard(_SEARCH_FINANCIAL_ITEMS_SQL)),
        {
            "tickers": tickers,
            "stmt_type": normalized_stmt_type,
            "keyword": normalized_keyword,
            "pattern": f"%{escape_like(normalized_keyword)}%" if normalized_keyword else None,
            "limit": max(1, min(100, limit)),
        },
    )
    rows = _mappings(result)
    return [
        {
            "stmt_type": row["stmt_type"],
            "item_name": row["item_name"],
            "unit": row["unit"],
            "periods": row["periods"],
            "latest_period": _to_isoformat(row["latest_period"]),
        }
        for row in rows
    ]


async def fetch_recent_reports(
    session: AsyncSession, tickers: list[str], limit: int = 5
) -> list[dict[str, object]]:
    result = await session.execute(
        text(guard(_RECENT_REPORTS_SQL)), {"tickers": tickers, "limit": limit}
    )
    rows = _mappings(result)
    return [
        {
            "report_idx": row["report_idx"],
            "report_date": _to_isoformat(row["report_date"]),
            "ticker": row["ticker"],
            "title": row["title"],
            "target_price": _to_float(row["target_price"]),
            "investment_opinion": row["investment_opinion"],
            "author": row["author"],
            "provider": row["provider"],
            "full_text_chars": row["full_text_chars"],
        }
        for row in rows
    ]


async def fetch_report_body(
    session: AsyncSession,
    tickers: list[str],
    report_idx: int,
    max_chars: int = 4000,
    offset: int = 0,
) -> dict[str, object] | None:
    max_chars = max(1000, min(8000, max_chars))
    offset = max(0, offset)
    result = await session.execute(
        text(guard(_REPORT_BODY_SQL)),
        {
            "tickers": tickers,
            "report_idx": str(report_idx),
            "max_chars": max_chars,
            "offset": offset,
        },
    )
    row = result.mappings().first()
    if row is None:
        return None
    report = cast(Mapping[str, object], row)
    return {
        "report_idx": report["report_idx"],
        "title": report["title"],
        "provider": report["provider"],
        "author": report["author"],
        "report_date": _to_isoformat(report["report_date"]),
        "target_price": _to_float(report["target_price"]),
        "investment_opinion": report["investment_opinion"],
        "full_text_chars": report["full_text_chars"],
        "body_text": report["body_text"],
        "truncated": report["truncated"],
    }


async def fetch_consensus(session: AsyncSession, tickers: list[str]) -> list[dict[str, object]]:
    result = await session.execute(text(guard(_CONSENSUS_SQL)), {"tickers": tickers})
    rows = _mappings(result)
    return [
        {
            "ticker": row["ticker"],
            "provider": row["provider"],
            "avg_target_price": _to_float(row["avg_target_price"]),
            "n": row["n"],
            "latest": _to_isoformat(row["latest"]),
        }
        for row in rows
    ]


async def search_reports_ilike(
    session: AsyncSession, tickers: list[str], keyword: str, limit: int = 5
) -> list[dict[str, object]]:
    result = await session.execute(
        text(guard(_SEARCH_REPORTS_SQL)),
        {
            "tickers": tickers,
            "keyword": keyword,
            "pattern": f"%{escape_like(keyword)}%",
            "limit": limit,
        },
    )
    rows = _mappings(result)
    return [
        {
            "report_idx": row["report_idx"],
            "report_date": _to_isoformat(row["report_date"]),
            "ticker": row["ticker"],
            "title": row["title"],
            "provider": row["provider"],
            "body_snippet": row["body_snippet"],
        }
        for row in rows
    ]


async def fetch_snapshot(session: AsyncSession, tickers: list[str]) -> list[dict[str, object]]:
    result = await session.execute(text(guard(_SNAPSHOT_SQL)), {"tickers": tickers})
    rows = _mappings(result)
    return [
        {
            "symbol": row["symbol"],
            "last_price": _to_float(row["last_price"]),
            "change": _to_float(row["change"]),
            "change_rate": _to_float(row["change_rate"]),
            "change_sign": row["change_sign"],
            "cumulative_volume": _to_float(row["cumulative_volume"]),
            "trade_strength": _to_float(row["trade_strength"]),
            "vi_trigger_price": _to_float(row["vi_trigger_price"]),
            "trading_halted": row["trading_halted"],
            "last_trade_time": row["last_trade_time"],
            "updated_at": _to_isoformat(row["updated_at"]),
        }
        for row in rows
    ]

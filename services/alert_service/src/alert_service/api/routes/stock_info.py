"""Toss-style stock information API over reference/serving tables."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text

from alert_service.api.routes.candles import _session_factory, _to_float


router = APIRouter(prefix="/api", tags=["stock_info"])


_META_SQL = text(
    """
    SELECT t.company_name AS stock_name,
           t.market,
           o.market_value_krw AS market_cap,
           COALESCE(o.company->'industry'->>'displayName', '미분류') AS industry_name,
           o.company->>'ceo' AS ceo_name,
           o.list_date AS listing_date
    FROM reference.bronze_market_ticker t
    LEFT JOIN reference.bronze_stock_overview o ON o.ticker = t.ticker
    WHERE t.ticker = :symbol
    """
)

_FINANCIALS_SQL = text(
    """
    SELECT stmt_type, period, item_name AS item, value, unit
    FROM reference.financial_metrics
    WHERE ticker = :symbol AND period_type = :period_type
    ORDER BY stmt_type, period DESC
    """
)

_LAST_PRICE_SQL = text(
    """
    SELECT last_price
    FROM serving.symbol_snapshot
    WHERE symbol = :symbol
    """
)

_STMT_KEYS = {"INC": "income", "BAL": "balance", "CAS": "cashflow"}
_EPS_ITEM = "*주당순이익"
_EQUITY_ITEM = "지배주주지분"
_SHARES_ITEM = "발행주식수"


def _iso_or_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _row_value(row: Any) -> float | None:
    return _to_float(row.value)


def _latest_eps(rows: list[Any]) -> float | None:
    for row in rows:
        if row.stmt_type == "INC" and row.item == _EPS_ITEM:
            return _row_value(row)
    return None


def _latest_bps(rows: list[Any]) -> tuple[float | None, str | None]:
    balance_by_period: dict[str, dict[str, float | None]] = {}
    for row in rows:
        if row.stmt_type != "BAL" or row.item not in {_EQUITY_ITEM, _SHARES_ITEM}:
            continue
        balance_by_period.setdefault(row.period, {})[row.item] = _row_value(row)

    missing_reason: str | None = None
    for _period, values in balance_by_period.items():
        equity = values.get(_EQUITY_ITEM)
        shares = values.get(_SHARES_ITEM)
        if equity is not None and shares is not None:
            if shares == 0:
                return None, "PBR: 발행주식수 0"
            return (equity * 1000) / shares, None
        if equity is None:
            missing_reason = "PBR: 지배주주지분 누락"
        if shares is None:
            missing_reason = "PBR: 발행주식수 누락"

    return None, missing_reason or "PBR: 재무항목 누락"


def _group_financials(rows: list[Any]) -> dict[str, list[dict[str, Any]]]:
    financials: dict[str, list[dict[str, Any]]] = {"income": [], "balance": [], "cashflow": []}
    for row in rows:
        key = _STMT_KEYS.get(row.stmt_type)
        if key is None:
            continue
        financials[key].append(
            {
                "item": row.item,
                "period": row.period,
                "value": _row_value(row),
                "unit": row.unit,
            }
        )
    return financials


def _overview_missing(row: Any) -> bool:
    return row.market_cap is None and row.ceo_name is None and row.listing_date is None


@router.get("/stock-info/{symbol}")
async def get_stock_info(
    request: Request,
    symbol: str,
    period_type: Annotated[str, Query()] = "Q",
) -> dict[str, Any]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        meta_result = await session.execute(_META_SQL, {"symbol": symbol})
        meta_row = meta_result.first()
        financials_result = await session.execute(
            _FINANCIALS_SQL, {"symbol": symbol, "period_type": period_type}
        )
        financial_rows = list(financials_result.all())
        price_result = await session.execute(_LAST_PRICE_SQL, {"symbol": symbol})
        price_row = price_result.first()

    if meta_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="stock not found")

    last_price = _to_float(price_row.last_price) if price_row is not None else None
    eps = _latest_eps(financial_rows)
    per = last_price / eps if last_price is not None and eps not in (None, 0) else None
    bps, pbr_missing_reason = _latest_bps(financial_rows)
    pbr = last_price / bps if last_price is not None and bps not in (None, 0) else None

    coverage_notes: list[str] = []
    if eps in (None, 0):
        coverage_notes.append("EPS 없음")
    if pbr is None and pbr_missing_reason is not None:
        coverage_notes.append(pbr_missing_reason)
    if last_price is None:
        coverage_notes.append("last_price 없음")
    if _overview_missing(meta_row):
        coverage_notes.append("overview 미적재")

    return {
        "meta": {
            "stock_name": meta_row.stock_name,
            "market": meta_row.market,
            "market_cap": meta_row.market_cap,
            "industry_name": meta_row.industry_name,
            "ceo_name": meta_row.ceo_name,
            "listing_date": _iso_or_str(meta_row.listing_date),
        },
        "financials": _group_financials(financial_rows),
        "indicators": {"eps": eps, "per": per, "pbr": pbr},
        "coverage_note": ", ".join(coverage_notes),
    }

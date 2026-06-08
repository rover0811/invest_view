from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from strands import ToolContext, tool
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.financial_items import (
    CONTROLLING_EQUITY,
    EPS,
    NET_INCOME,
    SHARES_OUTSTANDING,
    TOTAL_EQUITY,
    TOTAL_LIABILITIES,
)
from alert_service.agent.repository import fetch_indicator_inputs as repository_fetch_indicator_inputs


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


def _round(value: float | None, digits: int) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _latest_value(
    rows: Sequence[Mapping[str, object]], stmt_type: str, item: str
) -> tuple[float | None, str | None]:
    for row in rows:
        if row.get("stmt_type") == stmt_type and row.get("item") == item:
            value = row.get("value")
            if isinstance(value, int | float):
                return float(value), str(row.get("period"))
            return None, str(row.get("period"))
    return None, None


def _period_item_values(
    rows: Sequence[Mapping[str, object]], stmt_type: str, items: set[str]
) -> dict[str, dict[str, float | None]]:
    values_by_period: dict[str, dict[str, float | None]] = {}
    for row in rows:
        if row.get("stmt_type") != stmt_type or row.get("item") not in items:
            continue
        period = str(row.get("period"))
        value = row.get("value")
        values_by_period.setdefault(period, {})[str(row.get("item"))] = (
            float(value) if isinstance(value, int | float) else None
        )
    return values_by_period


def _latest_bps(rows: Sequence[Mapping[str, object]]) -> tuple[float | None, str | None, str | None]:
    values_by_period = _period_item_values(rows, "BAL", {CONTROLLING_EQUITY, SHARES_OUTSTANDING})
    missing_reason: str | None = None
    for period in sorted(values_by_period, reverse=True):
        values = values_by_period[period]
        equity = values.get(CONTROLLING_EQUITY)
        shares = values.get(SHARES_OUTSTANDING)
        if equity is not None and shares is not None:
            if shares == 0:
                return None, period, "BPS: 발행주식수 0"
            return equity / shares, period, None
        if equity is None:
            missing_reason = "BPS: 지배주주지분 누락"
        if shares is None:
            missing_reason = "BPS: 발행주식수 누락"
    return None, None, missing_reason or "BPS: 재무항목 누락"


def _latest_ratio(
    rows: Sequence[Mapping[str, object]],
    numerator_stmt: str,
    numerator_item: str,
    denominator_stmt: str,
    denominator_item: str,
    metric_name: str,
) -> tuple[float | None, str | None, str | None]:
    periods = sorted({str(row.get("period")) for row in rows}, reverse=True)
    for period in periods:
        numerator: float | None = None
        denominator: float | None = None
        for row in rows:
            if str(row.get("period")) != period:
                continue
            value = row.get("value")
            numeric = float(value) if isinstance(value, int | float) else None
            if row.get("stmt_type") == numerator_stmt and row.get("item") == numerator_item:
                numerator = numeric
            if row.get("stmt_type") == denominator_stmt and row.get("item") == denominator_item:
                denominator = numeric
        if numerator is not None and denominator is not None:
            if denominator == 0:
                return None, period, f"{metric_name}: 자본총계 0"
            return numerator / denominator, period, None
    return None, None, f"{metric_name}: 같은 기간 입력값 누락"


def compute_investment_indicators(
    ticker: str,
    financial_rows: Sequence[Mapping[str, object]],
    last_price: float | None,
) -> dict[str, object]:
    eps, eps_period = _latest_value(financial_rows, "INC", EPS)
    bps, bps_period, bps_reason = _latest_bps(financial_rows)
    roe, roe_period, roe_reason = _latest_ratio(
        financial_rows, "INC", NET_INCOME, "BAL", TOTAL_EQUITY, "ROE"
    )
    debt_ratio, debt_ratio_period, debt_ratio_reason = _latest_ratio(
        financial_rows, "BAL", TOTAL_LIABILITIES, "BAL", TOTAL_EQUITY, "부채비율"
    )

    latest_period = max(
        (str(row.get("period")) for row in financial_rows if row.get("period") is not None),
        default=None,
    )

    per: float | None = None
    per_reason: str | None = None
    if last_price is None:
        per_reason = "PER: last_price 없음"
    elif eps is None:
        per_reason = "PER: EPS 없음"
    elif eps == 0:
        per_reason = "PER: EPS 0"
    else:
        per = last_price / eps

    pbr: float | None = None
    pbr_reason: str | None = None
    if last_price is None:
        pbr_reason = "PBR: last_price 없음"
    elif bps is None:
        pbr_reason = bps_reason or "PBR: BPS 없음"
    elif bps == 0:
        pbr_reason = "PBR: BPS 0"
    else:
        pbr = last_price / bps

    result: dict[str, object] = {
        "ticker": ticker,
        "period": latest_period,
        "periods": {
            "eps": eps_period,
            "bps": bps_period,
            "roe": roe_period,
            "debt_ratio": debt_ratio_period,
        },
        "last_price": _round(last_price, 2),
        "eps": _round(eps, 2),
        "per": _round(per, 2),
        "bps": _round(bps, 2),
        "pbr": _round(pbr, 2),
        "roe": _round(roe, 4),
        "roe_pct": _round(roe * 100 if roe is not None else None, 2),
        "debt_ratio": _round(debt_ratio, 4),
        "debt_ratio_pct": _round(debt_ratio * 100 if debt_ratio is not None else None, 2),
        "currency_note": "EPS/BPS/현재가는 원 기준. BPS는 지배주주지분(천원)/발행주식수(천주)로 계산하며 천 단위가 상쇄됨. ROE/부채비율은 재무제표 천원 단위끼리 나눈 비율",
    }

    reasons: dict[str, str] = {}
    if eps is None:
        reasons["eps"] = "EPS: *주당순이익 누락"
    if per_reason is not None:
        reasons["per"] = per_reason
    if bps is None and bps_reason is not None:
        reasons["bps"] = bps_reason
    if pbr_reason is not None:
        reasons["pbr"] = pbr_reason
    if roe is None and roe_reason is not None:
        reasons["roe"] = roe_reason
    if debt_ratio is None and debt_ratio_reason is not None:
        reasons["debt_ratio"] = debt_ratio_reason
    if reasons:
        result["reasons"] = reasons
    return result


async def _get_investment_indicators_impl(
    session_factory: _SessionFactory, ticker: str
) -> dict[str, object]:
    async with session_factory() as session:
        inputs = await repository_fetch_indicator_inputs(session, [ticker], period_type="Y")
    financial_rows = [
        row for row in inputs.get("financials", []) if row.get("ticker") == ticker
    ]
    snapshot = next(
        (row for row in inputs.get("snapshots", []) if row.get("symbol") == ticker), None
    )
    last_price_value = snapshot.get("last_price") if snapshot is not None else None
    last_price = float(last_price_value) if isinstance(last_price_value, int | float) else None
    return compute_investment_indicators(ticker, financial_rows, last_price)


@tool(context=True)
async def get_investment_indicators(tool_context: ToolContext) -> dict[str, object]:
    """현재 종목의 투자지표(EPS, PER, PBR, BPS, ROE, 부채비율)를 재무제표와 현재가로 계산해 반환한다. PER/PBR/ROE/부채비율 질문 시 호출. 연간 기준."""
    return await _get_investment_indicators_impl(
        _require_session_factory(tool_context),
        _require_current_ticker(tool_context),
    )

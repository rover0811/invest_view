from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from typing import Protocol, cast

from strands import ToolContext, tool
from sqlalchemy.ext.asyncio import AsyncSession

from alert_service.agent.financial_items import UNIT, resolve_item_names
from alert_service.agent.repository import fetch_financials, search_financial_items


_SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class _ToolContextLike(Protocol):
    invocation_state: Mapping[str, object]


class _ChartSinkLike(Protocol):
    def put_nowait(self, item: object) -> None: ...


def _require_session_factory(tool_context: _ToolContextLike) -> _SessionFactory:
    session_factory = tool_context.invocation_state.get("session_factory")
    if not callable(session_factory):
        raise ValueError("session_factory is required in invocation_state")
    return cast(_SessionFactory, session_factory)


def _normalize_chart_type(chart_type: str) -> str:
    return "bar" if chart_type == "bar" else "line"


def _build_chart_spec(
    ticker: str,
    rows: list[dict[str, object]],
    item_names: list[str],
    chart_type: str,
) -> dict[str, object] | None:
    series_by_item: dict[str, list[dict[str, object]]] = {}
    first_unit = UNIT

    for row in rows:
        value = row.get("value")
        if value is None:
            continue
        if not isinstance(value, str | int | float | Decimal):
            continue

        item = row.get("item")
        period = row.get("period")
        if not isinstance(item, str) or not isinstance(period, str):
            continue

        unit = row.get("unit")
        if first_unit == UNIT and isinstance(unit, str) and unit:
            first_unit = unit

        series_by_item.setdefault(item, []).append({"x": period, "y": float(value)})

    ordered_items = [item for item in dict.fromkeys(item_names) if item in series_by_item]
    ordered_items.extend(item for item in series_by_item if item not in ordered_items)

    series = [
        {"name": item, "points": sorted(series_by_item[item], key=lambda point: str(point["x"]))}
        for item in ordered_items
    ]
    if not series:
        return None

    return {
        "chart_type": _normalize_chart_type(chart_type),
        "title": f"{ticker} 재무 추이",
        "x_label": "기간",
        "y_label": first_unit or "금액",
        "unit": first_unit,
        "series": series,
    }


async def _render_chart_impl(
    tool_context: _ToolContextLike,
    item_names: list[str],
    chart_type: str = "line",
    stmt_type: str = "INC",
    start_period: str | None = None,
    end_period: str | None = None,
) -> dict[str, object]:
    ticker = tool_context.invocation_state.get("current_ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("current_ticker is required in invocation_state")

    session_factory = _require_session_factory(tool_context)
    resolved_item_names = resolve_item_names(stmt_type, item_names) or []
    async with session_factory() as session:
        rows = await fetch_financials(
            session,
            [ticker],
            stmt_type,
            resolved_item_names,
            "Y",
            start_period,
            end_period,
        )

        spec = _build_chart_spec(ticker, rows, resolved_item_names, chart_type)
        if spec is None:
            matches = await search_financial_items(
                session, [ticker], stmt_type=stmt_type, keyword=None, limit=15
            )
            return {
                "status": "no_data",
                "items": resolved_item_names,
                "available_matches": [row["item_name"] for row in matches],
            }

    chart_sink = tool_context.invocation_state.get("chart_sink")
    if chart_sink is not None:
        cast(_ChartSinkLike, chart_sink).put_nowait(spec)

    series = cast(list[dict[str, object]], spec["series"])
    periods = {
        point["x"]
        for item in series
        for point in cast(list[dict[str, object]], item["points"])
    }
    return {
        "status": "success",
        "chart_type": spec["chart_type"],
        "items": [item["name"] for item in series],
        "periods": len(periods),
    }


@tool(context=True)
async def render_chart(
    tool_context: ToolContext,
    item_names: list[str],
    chart_type: str = "line",
    stmt_type: str = "INC",
    start_period: str | None = None,
    end_period: str | None = None,
) -> dict[str, object]:
    """현재 종목의 재무 추이를 차트로 생성한다. item_names는 한국어 재무 항목명 리스트(예: 매출액(수익), 영업이익). chart_type은 line 또는 bar. stmt_type은 INC/BAL/CAS. 연간(Y) 데이터만."""
    return await _render_chart_impl(
        tool_context,
        item_names,
        chart_type,
        stmt_type,
        start_period,
        end_period,
    )

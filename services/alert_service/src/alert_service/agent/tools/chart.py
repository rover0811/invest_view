from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUntypedFunctionDecorator=false, reportUnknownParameterType=false, reportUnknownArgumentType=false

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from math import isfinite
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


def _normalize_transform(transform: str) -> str:
    allowed = {"raw", "pct_change", "yoy_growth", "indexed_to_100", "cumulative_pct_change"}
    normalized = transform.strip().lower() if transform else "raw"
    return normalized if normalized in allowed else "raw"


def _infer_transform_from_item_names(item_names: list[str], transform: str) -> str:
    normalized = _normalize_transform(transform)
    if normalized != "raw":
        return normalized

    suffix_to_transform = (
        ("누적증감률", "cumulative_pct_change"),
        ("누적증가율", "cumulative_pct_change"),
        ("증가율", "yoy_growth"),
        ("성장률", "yoy_growth"),
        ("증감률", "yoy_growth"),
        ("변화율", "yoy_growth"),
        ("변동률", "yoy_growth"),
        ("지수화", "indexed_to_100"),
    )
    for item_name in item_names:
        stripped = item_name.strip()
        for suffix, inferred in suffix_to_transform:
            if stripped.endswith(suffix):
                return inferred
    return normalized


def _apply_transform(
    points: list[dict[str, object]], transform: str
) -> tuple[list[dict[str, object]], str | None, str]:
    normalized = _normalize_transform(transform)
    if normalized == "raw":
        return points, None, ""

    numeric_points: list[dict[str, object]] = []
    for point in points:
        value = point.get("y")
        if isinstance(value, int | float):
            numeric_value = float(value)
            if isfinite(numeric_value):
                numeric_points.append({"x": point["x"], "y": numeric_value})

    if normalized in {"pct_change", "yoy_growth"}:
        transformed: list[dict[str, object]] = []
        previous: float | None = None
        for point in numeric_points:
            current = cast(float, point["y"])
            if previous is not None and previous > 0:
                transformed.append(
                    {"x": point["x"], "y": ((current - previous) / abs(previous)) * 100}
                )
            previous = current
        return transformed, "%", "증가율(%)"

    base_index = next(
        (index for index, point in enumerate(numeric_points) if cast(float, point["y"]) > 0), None
    )
    if base_index is None:
        suffix = "지수(기준100)" if normalized == "indexed_to_100" else "누적증감률(%)"
        unit = "지수(기준100)" if normalized == "indexed_to_100" else "%"
        return [], unit, suffix
    base = cast(float, numeric_points[base_index]["y"])
    computable_points = numeric_points[base_index:]

    if normalized == "indexed_to_100":
        return [
            {"x": point["x"], "y": (cast(float, point["y"]) / base) * 100}
            for point in computable_points
        ], "지수(기준100)", "지수(기준100)"

    return [
        {"x": point["x"], "y": ((cast(float, point["y"]) / base) - 1) * 100}
        for point in computable_points
    ], "%", "누적증감률(%)"


def _build_chart_spec(
    ticker: str,
    rows: list[dict[str, object]],
    item_names: list[str],
    chart_type: str,
    transform: str = "raw",
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

    normalized_transform = _normalize_transform(transform)
    series: list[dict[str, object]] = []
    unit_override: str | None = None
    label_suffix = ""
    for item in ordered_items:
        sorted_points = sorted(series_by_item[item], key=lambda point: str(point["x"]))
        transformed_points, transformed_unit, transformed_suffix = _apply_transform(
            sorted_points, normalized_transform
        )
        if transformed_unit is not None:
            unit_override = transformed_unit
        if transformed_suffix:
            label_suffix = transformed_suffix
        if transformed_points:
            series.append(
                {
                    "name": f"{item} {transformed_suffix}" if transformed_suffix else item,
                    "points": transformed_points,
                }
            )
    if not series:
        return None

    y_unit = unit_override or first_unit or "금액"
    title_suffix = f" {label_suffix}" if label_suffix else ""

    return {
        "chart_type": _normalize_chart_type(chart_type),
        "title": f"{ticker} 재무 추이{title_suffix}",
        "x_label": "기간",
        "y_label": y_unit,
        "unit": y_unit,
        "series": series,
    }


async def _render_chart_impl(
    tool_context: _ToolContextLike,
    item_names: list[str],
    chart_type: str = "line",
    stmt_type: str = "INC",
    start_period: str | None = None,
    end_period: str | None = None,
    transform: str = "raw",
) -> dict[str, object]:
    ticker = tool_context.invocation_state.get("current_ticker")
    if not isinstance(ticker, str) or not ticker:
        raise ValueError("current_ticker is required in invocation_state")

    session_factory = _require_session_factory(tool_context)
    resolved_item_names = resolve_item_names(stmt_type, item_names) or []
    effective_transform = _infer_transform_from_item_names(item_names, transform)
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

        spec = _build_chart_spec(ticker, rows, resolved_item_names, chart_type, effective_transform)
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
    transform: str = "raw",
) -> dict[str, object]:
    """현재 종목의 재무 추이를 차트로 생성한다. item_names는 한국어 재무 항목명 리스트(예: 매출액(수익), 영업이익). chart_type은 line 또는 bar. stmt_type은 INC/BAL/CAS. transform은 raw/pct_change/yoy_growth/indexed_to_100/cumulative_pct_change. 연간(Y) 데이터만."""
    return await _render_chart_impl(
        tool_context,
        item_names,
        chart_type,
        stmt_type,
        start_period,
        end_period,
        transform,
    )

from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnusedCallResult=false, reportExplicitAny=false, reportUnknownParameterType=false, reportMissingParameterType=false

import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from alert_service.agent.financial_items import (
    CONTROLLING_EQUITY,
    EPS,
    NET_INCOME,
    SHARES_OUTSTANDING,
    TOTAL_EQUITY,
    TOTAL_LIABILITIES,
)
from alert_service.agent.tools.indicators import (
    _get_investment_indicators_impl,
    compute_investment_indicators,
    get_investment_indicators,
)


@dataclass(frozen=True)
class FakeToolContext:
    invocation_state: dict[str, Any]


class FakeSession:
    pass


class FakeSessionContextManager:
    session: FakeSession

    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeSessionFactory:
    session: FakeSession

    def __init__(self) -> None:
        self.session = FakeSession()

    def __call__(self) -> FakeSessionContextManager:
        return FakeSessionContextManager(self.session)


def _row(
    stmt_type: str, period: str, item: str, value: float, unit: str = "천원"
) -> dict[str, object]:
    return {
        "ticker": "005930",
        "stmt_type": stmt_type,
        "period": period,
        "item": item,
        "value": value,
        "unit": unit,
    }


def test_get_investment_indicators_is_strands_tool_with_expected_schema() -> None:
    assert hasattr(get_investment_indicators, "tool_spec")
    assert get_investment_indicators.tool_spec["name"] == "get_investment_indicators"

    params = inspect.signature(get_investment_indicators).parameters
    properties = get_investment_indicators.tool_spec["inputSchema"]["json"]["properties"]

    assert list(params) == ["tool_context"]
    assert properties == {}


def test_compute_investment_indicators_cancels_thousand_units_for_bps() -> None:
    rows = [
        _row("INC", "2024-12", EPS, 6605, "원/주"),
        _row("INC", "2024-12", NET_INCOME, 45_620_307_000),
        _row("BAL", "2024-12", CONTROLLING_EQUITY, 424_313_255_000),
        _row("BAL", "2024-12", SHARES_OUTSTANDING, 6_735_613, "천주"),
        _row("BAL", "2024-12", TOTAL_EQUITY, 440_389_723_000),
        _row("BAL", "2024-12", TOTAL_LIABILITIES, 131_838_000_000),
    ]

    result = compute_investment_indicators("005930", rows, 347_750)

    expected_bps = 424_313_255_000 / 6_735_613
    assert result["period"] == "2024-12"
    assert result["eps"] == 6605
    assert result["per"] == pytest.approx(round(347_750 / 6605, 2))
    assert result["bps"] == pytest.approx(round(expected_bps, 2))
    assert result["bps"] == pytest.approx(62_995.49)
    assert result["pbr"] == pytest.approx(round(347_750 / expected_bps, 2))
    assert result["pbr"] == pytest.approx(5.52)
    assert result["roe"] == pytest.approx(0.1036)
    assert result["roe_pct"] == pytest.approx(10.36)
    assert result["debt_ratio"] == pytest.approx(0.2994)
    assert result["debt_ratio_pct"] == pytest.approx(29.94)
    assert "천 단위가 상쇄" in str(result["currency_note"])
    assert "reasons" not in result


def test_compute_investment_indicators_returns_reasons_when_inputs_missing_or_zero() -> None:
    rows = [
        _row("INC", "2024-12", EPS, 0),
        _row("INC", "2024-12", NET_INCOME, 10),
        _row("BAL", "2024-12", CONTROLLING_EQUITY, 100),
        _row("BAL", "2024-12", SHARES_OUTSTANDING, 0),
        _row("BAL", "2024-12", TOTAL_EQUITY, 0),
        _row("BAL", "2024-12", TOTAL_LIABILITIES, 20),
    ]

    result = compute_investment_indicators("005930", rows, None)

    assert result["per"] is None
    assert result["bps"] is None
    assert result["pbr"] is None
    assert result["roe"] is None
    assert result["debt_ratio"] is None
    assert result["reasons"] == {
        "per": "PER: last_price 없음",
        "bps": "BPS: 발행주식수 0",
        "pbr": "PBR: last_price 없음",
        "roe": "ROE: 자본총계 0",
        "debt_ratio": "부채비율: 자본총계 0",
    }


async def test_get_investment_indicators_impl_uses_ambient_ticker_and_repository_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = FakeSessionFactory()
    calls: list[tuple[object, list[str], str]] = []

    async def fake_fetch_indicator_inputs(
        session: object, tickers: list[str], period_type: str = "Y"
    ) -> dict[str, list[dict[str, object]]]:
        calls.append((session, tickers, period_type))
        return {
            "financials": [
                _row("INC", "2024-12", EPS, 1000),
                _row("BAL", "2024-12", CONTROLLING_EQUITY, 6_000_000),
                _row("BAL", "2024-12", SHARES_OUTSTANDING, 100, "천주"),
                _row("INC", "2024-12", NET_INCOME, 30),
                _row("BAL", "2024-12", TOTAL_EQUITY, 100),
                _row("BAL", "2024-12", TOTAL_LIABILITIES, 50),
            ],
            "snapshots": [{"symbol": "005930", "last_price": 50_000.0}],
        }

    monkeypatch.setattr(
        "alert_service.agent.tools.indicators.repository_fetch_indicator_inputs",
        fake_fetch_indicator_inputs,
    )

    result = await _get_investment_indicators_impl(session_factory, "005930")

    assert calls == [(session_factory.session, ["005930"], "Y")]
    assert result["per"] == 50.0
    assert result["pbr"] == pytest.approx(0.83)
    assert result["roe"] == 0.3
    assert result["debt_ratio"] == 0.5


async def test_get_investment_indicators_tool_requires_ambient_context() -> None:
    context = FakeToolContext(invocation_state={})

    with pytest.raises(ValueError, match="session_factory"):
        await get_investment_indicators._tool_func(context)

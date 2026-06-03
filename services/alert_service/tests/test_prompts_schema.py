# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false

from datetime import datetime
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from alert_service.agent.prompts import SYSTEM_PROMPT
from alert_service.agent.schemas import AnalysisResponse


def test_system_prompt_is_nonempty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 0
    assert len(SYSTEM_PROMPT) <= 4000


def test_system_prompt_uses_structured_gemini_sections():
    expected_sections = (
        "<role>",
        "<task>",
        "<tools>",
        "<reasoning>",
        "<constraints>",
        "<output_format>",
        "<examples>",
    )
    for section in expected_sections:
        assert section in SYSTEM_PROMPT


def test_system_prompt_contains_trade_guardrail():
    assert "매매" in SYSTEM_PROMPT or "주문" in SYSTEM_PROMPT


def test_system_prompt_contains_prediction_guardrail():
    assert "예측" in SYSTEM_PROMPT


def test_system_prompt_contains_evidence_guardrail():
    assert "증거" in SYSTEM_PROMPT or "근거" in SYSTEM_PROMPT


def test_system_prompt_contains_korean_financial_term():
    assert any(term in SYSTEM_PROMPT for term in ("영업이익", "EBITDA", "itemNameKor"))


def test_system_prompt_contains_render_chart_guidance():
    assert "render_chart" in SYSTEM_PROMPT
    assert "item_names" in SYSTEM_PROMPT
    assert "stmt_type" in SYSTEM_PROMPT
    assert "INC/BAL/CAS" in SYSTEM_PROMPT
    assert "line/bar" in SYSTEM_PROMPT


def test_system_prompt_lists_all_market_analyst_tools():
    for tool_name in (
        "get_symbol_snapshot",
        "get_financials",
        "compare_financials",
        "search_financial_items",
        "render_chart",
        "get_recent_reports",
        "get_report_body",
        "get_consensus",
        "search_reports",
    ):
        assert tool_name in SYSTEM_PROMPT


def test_system_prompt_routes_realtime_trade_strength_to_snapshot():
    assert "get_symbol_snapshot" in SYSTEM_PROMPT
    assert "체결강도" in SYSTEM_PROMPT
    assert "실시간 시세" in SYSTEM_PROMPT
    assert "trade_strength" in SYSTEM_PROMPT
    assert ">100" in SYSTEM_PROMPT
    assert "<100" in SYSTEM_PROMPT


def test_system_prompt_requires_target_price_consensus_interpretation():
    assert "목표주가" in SYSTEM_PROMPT
    assert "get_consensus" in SYSTEM_PROMPT
    assert "get_recent_reports" in SYSTEM_PROMPT
    assert "get_report_body" in SYSTEM_PROMPT
    assert "최고가" in SYSTEM_PROMPT
    assert "최저가" in SYSTEM_PROMPT
    assert "근거" in SYSTEM_PROMPT
    assert "단정" in SYSTEM_PROMPT


def test_system_prompt_says_friendly_financial_item_names_are_resolved():
    assert "친숙한 한국어 항목명" in SYSTEM_PROMPT
    assert "backend" in SYSTEM_PROMPT or "백엔드" in SYSTEM_PROMPT
    assert "매출액" in SYSTEM_PROMPT
    assert "주당순이익" in SYSTEM_PROMPT
    assert "*" in SYSTEM_PROMPT
    assert "()" in SYSTEM_PROMPT or "괄호" in SYSTEM_PROMPT


def test_system_prompt_requires_financial_item_discovery_pattern():
    assert "search_financial_items" in SYSTEM_PROMPT
    assert "발견→정확한 이름 선택→조회" in SYSTEM_PROMPT
    assert "available_matches" in SYSTEM_PROMPT
    assert "같은 이름 반복" in SYSTEM_PROMPT
    assert "INC/BAL/CAS" in SYSTEM_PROMPT
    assert "매출액(수익)" in SYSTEM_PROMPT
    assert "*주당순이익" in SYSTEM_PROMPT


def test_system_prompt_does_not_advertise_unavailable_investment_indicators():
    assert "PER/PBR/ROE/부채비율" in SYSTEM_PROMPT
    assert "직접 제공되지 않습니다" in SYSTEM_PROMPT
    assert "직접 계산하지 않습니다" in SYSTEM_PROMPT


def test_system_prompt_allows_markdown_tables_for_report_comparison():
    assert "Markdown" in SYSTEM_PROMPT
    assert "표" in SYSTEM_PROMPT
    assert "리스트" in SYSTEM_PROMPT
    assert "기본" in SYSTEM_PROMPT
    assert "get_report_body" in SYSTEM_PROMPT
    assert "기능이 없다" in SYSTEM_PROMPT
    assert "거부" in SYSTEM_PROMPT


def test_analysis_response_constructs():
    now = datetime.now()
    resp = AnalysisResponse(
        summary="x",
        evidence=[],
        data_freshness=now,
        coverage_note=None,
    )
    assert resp.summary == "x"
    assert resp.evidence == []
    assert resp.data_freshness == now
    assert resp.coverage_note is None


def test_analysis_response_round_trips_model_dump():
    now = datetime.now()
    resp = AnalysisResponse(
        summary="x",
        evidence=[],
        data_freshness=now,
        coverage_note=None,
    )
    dumped = resp.model_dump()
    assert dumped["summary"] == "x"
    assert dumped["evidence"] == []
    assert dumped["data_freshness"] == now
    assert dumped["coverage_note"] is None


def test_analysis_response_with_coverage_note():
    resp = AnalysisResponse(
        summary="커버리지 외 종목",
        evidence=["출처: 없음"],
        data_freshness=datetime.now(),
        coverage_note="해당 종목은 서비스 커버리지(41종목) 밖입니다.",
    )
    dumped = resp.model_dump()
    assert dumped["coverage_note"] is not None

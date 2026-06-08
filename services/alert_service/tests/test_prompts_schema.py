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
        "<calculation_policy>",
        "<answerability_policy>",
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
    assert "transform" in SYSTEM_PROMPT
    assert "pct_change" in SYSTEM_PROMPT
    assert "yoy_growth" in SYSTEM_PROMPT
    assert "순수 재무제표 항목명" in SYSTEM_PROMPT
    assert "주당순이익 증가율" in SYSTEM_PROMPT
    assert 'transform="yoy_growth"' in SYSTEM_PROMPT
    assert "cumulative_pct_change" in SYSTEM_PROMPT
    assert "indexed_to_100" in SYSTEM_PROMPT


def test_system_prompt_requires_immediate_partial_execution_without_permission_ask():
    assert "보여드릴까요?" in SYSTEM_PROMPT
    assert "즉시 도구를 호출" in SYSTEM_PROMPT
    assert "가능한 부분" in SYSTEM_PROMPT
    assert "불가능한 일부만 제한" in SYSTEM_PROMPT


def test_system_prompt_guides_stock_price_chart_routing():
    # Old fallback (no yearly price data) must be gone; new routing to source="price" must be present.
    assert "주가 증가율" in SYSTEM_PROMPT
    assert 'source="price"' in SYSTEM_PROMPT
    assert 'render_chart(source="price")' in SYSTEM_PROMPT
    assert "백필된 연간 종가" in SYSTEM_PROMPT
    # Old fallback phrases must NOT appear
    assert "연간 주가 데이터가 없" not in SYSTEM_PROMPT
    assert "화면 왼쪽 캔들 차트" not in SYSTEM_PROMPT
    assert "가짜 연간 주가" not in SYSTEM_PROMPT


def test_system_prompt_lists_all_market_analyst_tools():
    for tool_name in (
        "get_symbol_snapshot",
        "get_financials",
        "compare_financials",
        "search_financial_items",
        "get_investment_indicators",
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


def test_system_prompt_routes_investment_indicators_to_tool():
    assert "get_investment_indicators" in SYSTEM_PROMPT
    assert "PER/PBR/ROE/EPS/BPS/부채비율" in SYSTEM_PROMPT
    assert "툴이 계산" in SYSTEM_PROMPT
    assert "그 결과를 인용" in SYSTEM_PROMPT


def test_system_prompt_allows_markdown_tables_for_report_comparison():
    assert "Markdown" in SYSTEM_PROMPT
    assert "표" in SYSTEM_PROMPT
    assert "리스트" in SYSTEM_PROMPT
    assert "기본" in SYSTEM_PROMPT
    assert "get_report_body" in SYSTEM_PROMPT
    assert "기능이 없다" in SYSTEM_PROMPT
    assert "거부" in SYSTEM_PROMPT


def test_system_prompt_allows_transparent_arithmetic_but_forbids_fabricated_raw_numbers():
    assert "<calculation_policy>" in SYSTEM_PROMPT
    assert "원천 수치(raw number)" in SYSTEM_PROMPT
    assert "도구가 반환하지 않은" in SYSTEM_PROMPT
    assert "단순하고 검증 가능한 산술" in SYSTEM_PROMPT
    assert "차이, 증감률, 비중, 단순 비율" in SYSTEM_PROMPT
    assert "거절은 마지막 수단" in SYSTEM_PROMPT
    assert "해당 기능 없음" in SYSTEM_PROMPT
    assert "가능한 부분을 먼저 수행" in SYSTEM_PROMPT


def test_system_prompt_mandates_report_body_reading_before_summary():
    assert "반드시 report_idx를 확보한 뒤" in SYSTEM_PROMPT
    assert "각 리포트에 get_report_body를 호출합니다" in SYSTEM_PROMPT
    assert "report_idx 목록 확보" in SYSTEM_PROMPT
    assert "각 report_idx마다 get_report_body 호출" in SYSTEM_PROMPT
    assert "본문 내용만 근거로 요약" in SYSTEM_PROMPT
    assert "제목만으로 요약하거나" in SYSTEM_PROMPT
    assert "본문이 제공되지 않아 제목으로 미루어" in SYSTEM_PROMPT
    assert "절대 금지" in SYSTEM_PROMPT
    assert "본문을 읽은 뒤에만 요약합니다" in SYSTEM_PROMPT
    assert "제목 추측 요약은 절대 금지" in SYSTEM_PROMPT


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

from datetime import datetime

from alert_service.agent.prompts import SYSTEM_PROMPT
from alert_service.agent.schemas import AnalysisResponse


def test_system_prompt_is_nonempty_string():
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 0


def test_system_prompt_contains_trade_guardrail():
    assert "매매" in SYSTEM_PROMPT or "주문" in SYSTEM_PROMPT


def test_system_prompt_contains_prediction_guardrail():
    assert "예측" in SYSTEM_PROMPT


def test_system_prompt_contains_evidence_guardrail():
    assert "증거" in SYSTEM_PROMPT or "근거" in SYSTEM_PROMPT


def test_system_prompt_contains_korean_financial_term():
    assert any(term in SYSTEM_PROMPT for term in ("영업이익", "EBITDA", "itemNameKor"))


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

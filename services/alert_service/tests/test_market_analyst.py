from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from alert_service.agent.market_analyst import AGENT_TOOLS, build_market_analyst_agent
from alert_service.container import Container
from alert_service.db.session import create_engine, create_session_factory


EXPECTED_TOOL_NAMES = {
    "get_symbol_snapshot",
    "get_financials",
    "compare_financials",
    "search_financial_items",
    "render_chart",
    "get_recent_reports",
    "get_report_body",
    "get_consensus",
    "search_reports",
}


def test_market_analyst_registers_single_agent_tools_and_prompt(monkeypatch):
    fake_model = MagicMock()
    build_model = MagicMock(side_effect=AssertionError("model factory should not run"))
    monkeypatch.setattr("alert_service.agent.market_analyst.build_gemini_model", build_model)

    agent = build_market_analyst_agent(MagicMock(), model=fake_model)

    assert set(agent.tool_names) == EXPECTED_TOOL_NAMES
    assert len(agent.tool_names) == len(EXPECTED_TOOL_NAMES)
    assert getattr(agent, "_default_structured_output_model") is None
    build_model.assert_not_called()


def test_agent_tools_are_registered_in_phase_one_order():
    assert [tool.tool_name for tool in AGENT_TOOLS] == [
        "get_symbol_snapshot",
        "get_financials",
        "compare_financials",
        "search_financial_items",
        "render_chart",
        "get_recent_reports",
        "get_report_body",
        "get_consensus",
        "search_reports",
    ]


def test_container_build_market_analyst_delegates(monkeypatch):
    config = MagicMock()
    model = MagicMock()
    messages = [MagicMock()]
    conversation_manager = MagicMock()
    built_agent = MagicMock()
    build_agent = MagicMock(return_value=built_agent)
    monkeypatch.setattr("alert_service.container.build_market_analyst_agent", build_agent)

    container = Container.__new__(Container)
    container.config = config

    agent = container.build_market_analyst(
        model=model,
        messages=messages,
        conversation_manager=conversation_manager,
    )

    assert agent is built_agent
    build_agent.assert_called_once_with(
        config,
        model=model,
        messages=messages,
        conversation_manager=conversation_manager,
    )


def _has_application_default_credentials() -> bool:
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        return Path(credentials_path).exists()

    try:
        import google.auth

        google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except Exception:
        return False
    return True


@pytest.mark.qa
def test_real_gemini_agent_smoke():
    project = os.environ.get("ALERT_SERVICE_GCP_PROJECT")
    database_url = os.environ.get("ALERT_SERVICE_DATABASE_URL")
    if not project or not _has_application_default_credentials():
        pytest.skip("no GCP creds")
    if not database_url:
        pytest.skip("no database url")

    config = SimpleNamespace(
        gcp_project=project,
        gcp_location=os.environ.get("ALERT_SERVICE_GCP_LOCATION", "us-central1"),
        gemini_model_id=os.environ.get("ALERT_SERVICE_GEMINI_MODEL_ID", "gemini-2.5-flash"),
        gemini_temperature=float(os.environ.get("ALERT_SERVICE_GEMINI_TEMPERATURE", "0.3")),
        gemini_max_output_tokens=int(os.environ.get("ALERT_SERVICE_GEMINI_MAX_OUTPUT_TOKENS", "4096")),
    )
    engine = create_engine(database_url)
    session_factory = create_session_factory(engine)
    agent = build_market_analyst_agent(config)

    response = agent(
        "최근 3년 영업이익 추이는?",
        invocation_state={"current_ticker": "005930", "session_factory": session_factory},
    )

    assert str(response).strip()

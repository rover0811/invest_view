from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnusedCallResult=false

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from strands.agent.conversation_manager import SlidingWindowConversationManager

from alert_service.agent.history import build_conversation_manager, to_strands_messages
from alert_service.agent.market_analyst import build_market_analyst_agent
from alert_service.db.session import create_engine, create_session_factory


def test_to_strands_messages_maps_user_and_complete_assistant_turns_exactly():
    active_path = [
        {"role": "user", "content": "q1", "status": "complete"},
        {"role": "assistant", "content": "a1", "status": "complete"},
    ]

    assert to_strands_messages(active_path) == [
        {"role": "user", "content": [{"text": "q1"}]},
        {"role": "assistant", "content": [{"text": "a1"}]},
    ]


def test_to_strands_messages_skips_non_chat_roles_and_incomplete_assistant_turns():
    active_path = [
        {"role": "system", "content": "system prompt", "status": "complete"},
        {"role": "tool", "content": "tool output", "status": "complete"},
        {"role": "assistant", "content": "partial", "status": "streaming"},
        {"role": "assistant", "content": "failed", "status": "error"},
        {"role": "assistant", "content": "stopped", "status": "interrupted"},
        {"role": "user", "content": "keep user even if interrupted", "status": "interrupted"},
        {"role": "assistant", "content": "done", "status": "complete"},
    ]

    assert to_strands_messages(active_path) == [
        {"role": "user", "content": [{"text": "keep user even if interrupted"}]},
        {"role": "assistant", "content": [{"text": "done"}]},
    ]


def test_build_conversation_manager_returns_sliding_window_instance():
    assert isinstance(build_conversation_manager(), SlidingWindowConversationManager)


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
def test_real_gemini_agent_multiturn_smoke():
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
    agent = build_market_analyst_agent(
        config,
        messages=[
            {"role": "user", "content": [{"text": "삼성전자 영업이익 추이 알려줘"}]},
            {"role": "assistant", "content": [{"text": "최근 영업이익은 연도별로 변동성이 있었습니다."}]},
        ],
        conversation_manager=build_conversation_manager(),
    )

    response = agent(
        "방금 그 수치 다시 요약해줘",
        invocation_state={"current_ticker": "005930", "session_factory": session_factory},
    )

    assert str(response).strip()

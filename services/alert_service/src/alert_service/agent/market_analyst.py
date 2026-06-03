from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportExplicitAny=false, reportAny=false

from typing import Any

from strands import Agent

from alert_service.agent.model import build_gemini_model
from alert_service.agent.prompts import SYSTEM_PROMPT
from alert_service.agent.tools.chart import render_chart
from alert_service.agent.tools.discovery import search_financial_items
from alert_service.agent.tools.financial import compare_financials, get_financials
from alert_service.agent.tools.market import get_symbol_snapshot
from alert_service.agent.tools.reports import get_consensus, get_recent_reports, get_report_body
from alert_service.agent.tools.search import search_reports


AGENT_TOOLS: list[Any] = [
    get_symbol_snapshot,
    get_financials,
    compare_financials,
    search_financial_items,
    render_chart,
    get_recent_reports,
    get_report_body,
    get_consensus,
    search_reports,
]


def build_market_analyst_agent(
    config: Any,
    *,
    model: Any | None = None,
    messages: Any | None = None,
    conversation_manager: Any | None = None,
) -> Agent:
    """Build the single market analyst Strands agent."""
    agent_model = model if model is not None else build_gemini_model(config)
    kwargs: dict[str, Any] = {
        "model": agent_model,
        "system_prompt": SYSTEM_PROMPT,
        "tools": AGENT_TOOLS,
    }
    if messages is not None:
        kwargs["messages"] = messages
    if conversation_manager is not None:
        kwargs["conversation_manager"] = conversation_manager
    return Agent(**kwargs)

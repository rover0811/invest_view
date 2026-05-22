import httpx
import pytest

from agent_trading.alert_client import AlertClient, AlertClientConfig
from agent_trading.context_builder import AgentContextBuilder


@pytest.mark.asyncio
async def test_context_builder_aggregates_alerts_and_patterns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/alerts":
            return httpx.Response(200, json={"items": [{"alert_type": "SURGE", "symbol": "005930"}]})
        if request.url.path == "/patterns":
            return httpx.Response(200, json={"items": [{"pattern_type": "GOLDEN_CROSS", "symbol": "005930"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://alert-serving:8080") as http:
        client = AlertClient(AlertClientConfig(), client=http)
        builder = AgentContextBuilder(client)

        context = await builder.build("005930")

    assert context.symbol == "005930"
    assert context.alerts == [{"alert_type": "SURGE", "symbol": "005930"}]
    assert context.patterns == [{"pattern_type": "GOLDEN_CROSS", "symbol": "005930"}]
    assert context.has_signal() is True


@pytest.mark.asyncio
async def test_context_builder_no_signal_when_both_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://alert-serving:8080") as http:
        client = AlertClient(AlertClientConfig(), client=http)
        builder = AgentContextBuilder(client)

        context = await builder.build("005930")

    assert context.has_signal() is False

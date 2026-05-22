from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alert_client import AlertClient


@dataclass
class AgentContext:
    symbol: str
    alerts: list[dict[str, Any]]
    patterns: list[dict[str, Any]]

    def has_signal(self) -> bool:
        return bool(self.alerts or self.patterns)


class AgentContextBuilder:

    def __init__(self, alert_client: AlertClient) -> None:
        self._alert_client = alert_client

    async def build(self, symbol: str, *, limit: int = 10) -> AgentContext:
        alerts = await self._alert_client.list_alerts(symbol, limit=limit)
        patterns = await self._alert_client.list_patterns(symbol, limit=limit)
        return AgentContext(symbol=symbol, alerts=alerts, patterns=patterns)

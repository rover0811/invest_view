from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class AlertClientConfig:
    base_url: str = "http://alert-serving:8080"
    timeout_seconds: float = 3.0


class AlertClient:

    def __init__(self, config: AlertClientConfig | None = None, *, client: httpx.AsyncClient | None = None) -> None:
        self._config = config or AlertClientConfig()
        self._client = client or httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=self._config.timeout_seconds,
        )

    async def list_alerts(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        response = await self._client.get("/alerts", params={"symbol": symbol, "limit": limit})
        response.raise_for_status()
        return response.json().get("items", [])

    async def list_patterns(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        response = await self._client.get("/patterns", params={"symbol": symbol, "limit": limit})
        response.raise_for_status()
        return response.json().get("items", [])

    async def aclose(self) -> None:
        await self._client.aclose()

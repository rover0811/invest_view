from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tick_persistence.observability.metrics import TickMetrics

logger = logging.getLogger(__name__)

_STALENESS_QUERY = text(
    "SELECT symbol, EXTRACT(EPOCH FROM (now() - last_event_ts)) AS staleness_seconds "
    "FROM serving.symbol_snapshot WHERE last_event_ts IS NOT NULL"
)


class FreshnessMonitor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        metrics: TickMetrics,
        interval_seconds: float = 5.0,
    ) -> None:
        self._session_factory = session_factory
        self._metrics = metrics
        self._interval = interval_seconds
        self._stop = asyncio.Event()

    async def refresh_once(self) -> dict[str, float]:
        async with self._session_factory() as session:
            result = await session.execute(_STALENESS_QUERY)
            rows = result.all()
        staleness: dict[str, float] = {}
        for symbol, seconds in rows:
            value = float(seconds)
            staleness[str(symbol)] = value
            self._metrics.set_snapshot_staleness(str(symbol), value)
        return staleness

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.refresh_once()
            except Exception as exc:
                logger.warning("freshness refresh failed (continuing): %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

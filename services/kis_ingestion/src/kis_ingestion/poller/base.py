import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Generic, Protocol, TypeVar
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

RecordT = TypeVar("RecordT")
Clock = Callable[[], datetime]


def now_kst() -> datetime:
    return datetime.now(KST)


class PollerTask(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def interval_seconds(self) -> float: ...

    def is_active(self, now: datetime) -> bool: ...

    async def run_once(self, now: datetime) -> int: ...


class Poller(ABC, Generic[RecordT]):
    """Common poller contract: poll interval, active-hours condition, one fetch, publish."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def interval_seconds(self) -> float: ...

    @abstractmethod
    def is_active(self, now: datetime) -> bool: ...

    @abstractmethod
    async def fetch(self, now: datetime) -> Sequence[RecordT]: ...

    @abstractmethod
    async def publish(self, records: Sequence[RecordT]) -> int: ...

    async def run_once(self, now: datetime) -> int:
        records = await self.fetch(now)
        if not records:
            return 0
        return await self.publish(records)


async def run_poller(poller: PollerTask, stop: asyncio.Event, clock: Clock = now_kst) -> None:
    logger.info("Poller started name=%s interval=%.1fs", poller.name, poller.interval_seconds)
    while not stop.is_set():
        now = clock()
        if poller.is_active(now):
            try:
                published = await poller.run_once(now)
                logger.info("Poller cycle done name=%s published=%d", poller.name, published)
            except Exception:
                logger.exception("Poller cycle failed name=%s", poller.name)
        try:
            _ = await asyncio.wait_for(stop.wait(), timeout=poller.interval_seconds)
        except TimeoutError:
            pass
    logger.info("Poller stopped name=%s", poller.name)


async def run_pollers(pollers: Sequence[PollerTask], stop: asyncio.Event, clock: Clock = now_kst) -> None:
    _ = await asyncio.gather(*(run_poller(poller, stop, clock) for poller in pollers))

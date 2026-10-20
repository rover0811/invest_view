import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class SharedRateLimiter:
    """Sliding-window limiter shared by every poller that uses the same KIS app key."""

    def __init__(
        self,
        max_calls: int,
        period_seconds: float = 1.0,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be positive")
        self._max_calls: int = max_calls
        self._period: float = period_seconds
        self._clock: Clock = clock
        self._sleep: Sleep = sleep
        self._calls: deque[float] = deque()
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def max_calls(self) -> int:
        return self._max_calls

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self._period:
                    _ = self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                await self._sleep(self._period - (now - self._calls[0]))

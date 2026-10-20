import asyncio

import pytest

from kis_ingestion.poller.rate_limiter import SharedRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
async def test_allows_max_calls_within_one_period_without_waiting():
    clock = FakeClock()
    limiter = SharedRateLimiter(3, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await limiter.acquire()

    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_waits_until_oldest_call_leaves_window():
    clock = FakeClock()
    limiter = SharedRateLimiter(2, clock=clock, sleep=clock.sleep)

    await limiter.acquire()
    clock.now = 0.4
    await limiter.acquire()
    await limiter.acquire()

    assert clock.sleeps == [pytest.approx(0.6)]
    assert clock.now == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_budget():
    clock = FakeClock()
    limiter = SharedRateLimiter(2, clock=clock, sleep=clock.sleep)

    _ = await asyncio.gather(*(limiter.acquire() for _ in range(5)))

    assert clock.now == pytest.approx(2.0)


def test_rejects_invalid_limits():
    with pytest.raises(ValueError):
        _ = SharedRateLimiter(0)
    with pytest.raises(ValueError):
        _ = SharedRateLimiter(1, period_seconds=0)

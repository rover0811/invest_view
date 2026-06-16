"""Property-based tests for snapshot ordering invariants (I2 / I3 / I4).

These tests assert the event-time ordering invariants from
``tests/INVARIANTS.md`` against the conditional snapshot upsert in
``tick_persistence/repository/snapshot.py`` (``WHERE last_event_ts IS NULL OR
excluded.last_event_ts >= existing``), using the canonical ``last_event_ts``
timestamptz derived from ``business_date`` + ``trade_time`` in KST. They guard
against any regression to unconditional last-writer-wins, which let an
out-of-order (older) tick overwrite a fresher snapshot.

ORDERING PROXY NOTE
-------------------
``serving.symbol_snapshot`` has no canonical ``event_ts`` column yet — T11 adds
``last_event_ts timestamptz`` derived from ``business_date`` + ``trade_time`` in the
``Asia/Seoul`` zone, and the current ``upsert_snapshot`` does not even persist
``business_date``. Until then the only stored ordering signal is
``last_trade_time`` (HHMMSS text). The ``out_of_order_ticks`` strategy keeps every
tick on a single ``business_date`` with *strictly-increasing* ``trade_time``, so
within one property example ``last_trade_time`` is a strict total order and a sound
proxy for the canonical event time. Assertions are written against the INTENDED
invariant (max-event-time tick wins, ordering key never regresses) so they go GREEN
once T11/T13 land the timestamptz comparison.

The boundary test (I4) deliberately crosses midnight (``235959`` -> next-day
``000001``). That is exactly the case a ``trade_time``-text-only guard inverts
(``"235959" > "000001"`` lexically). It encodes the expected behavior and may FAIL
now — correct RED until the canonical ``TIMESTAMPTZ`` comparison from T11 lands.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from hypothesis import given
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tick_persistence.db.models import SymbolSnapshot
from tick_persistence.repository.snapshot import SnapshotRepository

from tests.strategies import (
    COMMON_HYPOTHESIS_SETTINGS,
    hypothesis_db_session,
    out_of_order_ticks,
)

pytestmark = pytest.mark.qa


def _event_key(tick: dict[str, Any]) -> tuple[str, str]:
    """Canonical event-time ordering proxy ``(business_date, trade_time)``.

    T11 replaces this with a real ``event_ts timestamptz``. Within
    ``out_of_order_ticks`` every tick shares one ``business_date`` and carries a
    strictly-increasing ``trade_time``, so this tuple is a strict total order over
    the example's ticks (no two ticks share an event time — avoids the mirror trap).
    """
    return (str(tick["business_date"]), str(tick["trade_time"]))


async def _read_snapshot(session: AsyncSession, symbol: str) -> SymbolSnapshot | None:
    result = await session.execute(
        sa.select(SymbolSnapshot)
        .where(SymbolSnapshot.symbol == symbol)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


@asynccontextmanager
async def _rolled_back_savepoint(session: AsyncSession) -> AsyncIterator[None]:
    """Per-example DB isolation: run inside a SAVEPOINT that is always rolled back.

    Hypothesis reuses the function-scoped ``hypothesis_db_session`` across every
    generated example, so each example must roll its writes back (never release the
    savepoint) to keep the next example clean. On assertion failure the savepoint is
    still rolled back and the ``AssertionError`` propagates (RED), keeping the
    connection usable while Hypothesis shrinks to a minimal counterexample.
    """
    nested = await session.begin_nested()
    try:
        yield
    finally:
        if nested.is_active:
            await nested.rollback()


@COMMON_HYPOTHESIS_SETTINGS
@given(ticks=out_of_order_ticks())
async def test_snapshot_reflects_max_event_time_tick(
    hypothesis_db_session: AsyncSession,
    ticks: list[dict[str, Any]],
) -> None:
    """I2: after processing same-symbol ticks in PERMUTED arrival order, the snapshot
    must reflect the tick with the maximum canonical event time — not the last arrival.

    RED: the current unconditional LWW upsert keeps whichever tick arrived last, so
    any permutation whose final arrival is not the max-event-time tick lets an older
    tick win. Hypothesis shrinks to the minimal ``[t1, t0]`` swap.
    """
    repo = SnapshotRepository()
    symbol = ticks[0]["symbol"]
    newest = max(ticks, key=_event_key)

    async with _rolled_back_savepoint(hypothesis_db_session):
        for tick in ticks:
            await repo.upsert_snapshot(hypothesis_db_session, tick)

        snapshot = await _read_snapshot(hypothesis_db_session, symbol)
        assert snapshot is not None

        # INVARIANT I2: snapshot == max-event-time tick. last_trade_time is the
        # crispest signal (strictly-increasing -> never collides). T11 compares the
        # canonical event_ts; last_trade_time still stores the winning tick's time.
        assert snapshot.last_trade_time == newest["trade_time"], (
            "stale snapshot (older tick won): kept last_trade_time="
            f"{snapshot.last_trade_time!r} but the max-event-time tick is "
            f"{newest['trade_time']!r}; arrival order let a stale tick overwrite it"
        )
        assert snapshot.last_price == newest["price"], (
            f"stale price: kept {snapshot.last_price} but the max-event-time tick's "
            f"price is {newest['price']}"
        )


@COMMON_HYPOTHESIS_SETTINGS
@given(ticks=out_of_order_ticks())
async def test_snapshot_ordering_key_never_regresses(
    hypothesis_db_session: AsyncSession,
    ticks: list[dict[str, Any]],
) -> None:
    """I3: as ticks arrive (some fresh, some stale), the snapshot ordering key must be
    monotonic non-decreasing — a stale tick may land in bronze but must never regress
    serving state.

    RED: under LWW the snapshot ordering key after arrival *i* equals arrival *i*'s
    own event time, so any non-sorted arrival sequence produces a descent the first
    time a stale tick arrives after a fresher one.
    """
    repo = SnapshotRepository()
    symbol = ticks[0]["symbol"]

    # Ordering proxy is last_trade_time (HHMMSS). All ticks in one example share a
    # business_date, so lexical HHMMSS comparison == chronological. T11 swaps this for
    # the canonical event_ts timestamptz read off the snapshot row.
    previous_key: str | None = None

    async with _rolled_back_savepoint(hypothesis_db_session):
        for tick in ticks:
            await repo.upsert_snapshot(hypothesis_db_session, tick)
            snapshot = await _read_snapshot(hypothesis_db_session, symbol)
            assert snapshot is not None
            current_key = str(snapshot.last_trade_time)

            if previous_key is not None:
                # INVARIANT I3: ordering key must not move backward.
                assert current_key >= previous_key, (
                    "snapshot ordering key regressed: "
                    f"{previous_key!r} -> {current_key!r} — a stale tick overwrote a "
                    "fresher snapshot (LWW has no event-time guard)"
                )
            previous_key = current_key


def _boundary_tick(
    *, symbol: str, business_date: str, trade_time: str, price: int
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "price": price,
        "trade_volume": 1,
        "vwap": Decimal(str(price)),
        "change": price - 70_000,
        "change_rate": Decimal("1.23"),
        "change_sign": "2",
        "cumulative_volume": 1_000_000 + price,
        "trade_strength": Decimal("105.50"),
        "vi_trigger_price": price + 1_000,
        "trading_halted": "0",
        "trade_time": trade_time,
        "business_date": business_date,
    }


async def _process_in_order(
    db_session_factory: async_sessionmaker[AsyncSession],
    repo: SnapshotRepository,
    ticks: list[dict[str, Any]],
) -> SymbolSnapshot:
    symbol = ticks[0]["symbol"]
    for tick in ticks:
        async with db_session_factory() as session:
            await repo.upsert_snapshot(session, tick)
            await session.commit()
    async with db_session_factory() as session:
        snapshot = await _read_snapshot(session, symbol)
    assert snapshot is not None
    return snapshot


async def test_snapshot_boundary_next_day_wins_regardless_of_arrival(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """I4: a next-day ``00:00:01`` tick is canonically newer than a prev-day
    ``23:59:59`` tick and must win regardless of arrival order — midnight must not
    invert order when the HHMMSS text wraps (``"235959" > "000001"`` lexically).

    RED: the current LWW upsert keeps the last arrival, and a naive HHMMSS-text guard
    would also keep ``235959``. Both fail the "next-day wins" invariant in the
    arrival order where the prev-day late-night tick lands last. T11's canonical
    ``TIMESTAMPTZ`` (``business_date`` + ``trade_time`` in KST) + T13's conditional
    upsert make it GREEN. ``upsert_snapshot`` does not persist ``business_date`` yet,
    so this asserts the intended post-T11/T13 behavior.
    """
    repo = SnapshotRepository()
    prev_night_price = 70_000
    next_day_price = 80_000

    # Order A — prev-day 235959 first, then next-day 000001 (next-day arrives last).
    # Here LWW already agrees with the invariant; it pins the control direction.
    sym_a = "100001"
    snapshot_a = await _process_in_order(
        db_session_factory,
        repo,
        [
            _boundary_tick(symbol=sym_a, business_date="20260601", trade_time="235959", price=prev_night_price),
            _boundary_tick(symbol=sym_a, business_date="20260602", trade_time="000001", price=next_day_price),
        ],
    )
    assert snapshot_a.last_price == next_day_price
    assert snapshot_a.last_trade_time == "000001"

    # Order B — next-day 000001 first, then prev-day 235959 (prev-night arrives last).
    # The invariant still requires the next-day tick to win. LWW keeps prev-night => RED.
    sym_b = "100002"
    snapshot_b = await _process_in_order(
        db_session_factory,
        repo,
        [
            _boundary_tick(symbol=sym_b, business_date="20260602", trade_time="000001", price=next_day_price),
            _boundary_tick(symbol=sym_b, business_date="20260601", trade_time="235959", price=prev_night_price),
        ],
    )
    assert snapshot_b.last_price == next_day_price, (
        "boundary inversion: kept "
        f"{snapshot_b.last_price} (prev-day 23:59:59) but the next-day 00:00:01 tick "
        "is canonically newer and must win regardless of arrival order"
    )
    assert snapshot_b.last_trade_time == "000001", (
        "boundary inversion: kept last_trade_time="
        f"{snapshot_b.last_trade_time!r}; canonical event time requires next-day "
        "'000001' to win over prev-day '235959'"
    )

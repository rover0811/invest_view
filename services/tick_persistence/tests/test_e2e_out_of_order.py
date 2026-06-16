"""End-to-end (handler-level) verification that the TWO price-staleness fixes work
TOGETHER on a synthetic out-of-order + duplicate scenario.

This is T17 of ``.sisyphus/plans/price-staleness-fix.md``. It drives the REAL
``TickHandler`` pipeline (bronze insert -> snapshot upsert -> silver aggregation),
combining at the handler boundary the two invariants that ``test_dedup_invariants.py``
(I1) and ``test_snapshot_invariants.py`` (I2/I3/I4) prove independently at the
repository layer:

  I1 - Idempotency (T12, ``repository/tick_history.py``):
       bronze stores exactly ONE row per distinct ``event_id``. The same logical
       tick republished at a DIFFERENT Kafka ``topic:partition:offset`` does NOT add
       a bronze row -- offset is audit lineage, ``event_id`` is business identity.

  I2 - Stale-block / total-order (T13, ``repository/snapshot.py``):
       ``serving.symbol_snapshot`` reflects the MAX canonical-event-time tick
       (``last_price`` / ``last_event_ts`` / ``last_trade_time``), regardless of
       arrival order and regardless of the duplicate. An OLDER tick that arrives
       LAST (and carries a DIFFERENT, higher price) must NOT overwrite the snapshot.

Why this is a true E2E and not a mirror test:
  * It runs the real handler, so the early-return-on-duplicate path AND the
    conditional snapshot ``WHERE excluded.last_event_ts >= existing`` guard are both
    exercised through the same transaction the production consumer uses.
  * The max-event-time tick is deliberately NOT the last arrival, and the last
    arrival carries a *different* price (70500 vs the winner's 70200), so a
    last-writer-wins regression would produce a visibly wrong ``last_price``.
  * The duplicate is republished at a *new offset* (same payload), so only
    ``event_id`` collides -- exactly the idempotency contract under test.

------------------------------------------------------------------------------
[OPERATOR] Live weekday market-hours validation path (non-blocking)
------------------------------------------------------------------------------
The automated test above is the deterministic, cluster-free proof. For a live
end-to-end check against the real persistence path on the homelab k3s cluster,
craft an out-of-order + duplicate burst with the synthetic injector. ``make
inject-tick`` runs ``scripts/fake_tick_generator.py`` as a k8s Job against the
Strimzi bootstrap (see ``README.md`` OP-2 and ``Makefile`` target ``inject-tick``).

To reproduce THIS scenario live (out-of-order + duplicate for one symbol):
  1. Build a short, hand-ordered tick list for ONE symbol where ``trade_time``
     (business_date+trade_time) is strictly increasing, then publish them in a
     PERMUTED order so the max-event-time tick is NOT last. Re-publish one tick a
     second time (the producer assigns a new Kafka offset; the payload-derived
     ``event_id`` is unchanged) to exercise idempotency. A minimal variant of
     ``scripts/fake_tick_generator.py`` (publish an explicit ordered list, shuffle
     the publish order, send one tick twice) does this; the default generator emits
     random in-order ticks and is sufficient for a smoke check but not for ordering.
  2. Apply / run the injector (cluster context only -- NOT the macbook kind config):
         make inject-tick
  3. Verify on the cluster DB (credentials live in the ``invest-db-credentials``
     secret, user ``invest`` / db ``invest_view`` -- NOT postgres/postgres):
         # I1 idempotency: total == distinct event_id (no duplicate rows)
         SELECT count(*) AS total, count(DISTINCT event_id) AS distinct_ids
         FROM bronze.tick_history WHERE symbol = '<symbol>';
         # I2 stale-block: snapshot == max-event-time tick, not the last arrival
         SELECT symbol, last_price, last_trade_time, last_event_ts
         FROM serving.symbol_snapshot WHERE symbol = '<symbol>';
     Expected: total == distinct_ids, and last_price/last_event_ts equal the
     max-event-time tick you published (not the last-arriving stale one).
  NOTE: real ticks only flow on weekdays 09:00-15:30 KST; the synthetic injector
  works any time. This live path is a non-blocking operator confirmation -- the
  automated test below is the gating proof.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from hypothesis import given, settings

from tests.strategies import COMMON_HYPOTHESIS_SETTINGS, out_of_order_ticks
from tick_persistence.aggregation.ohlc import FiveMinuteAggregator
from tick_persistence.db.models import Symbol5mMetrics, SymbolSnapshot, TickHistory
from tick_persistence.event_id import compute_event_id
from tick_persistence.handler import TickHandler
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository

pytestmark = pytest.mark.qa

KST = ZoneInfo("Asia/Seoul")
_SYMBOL = "005930"
_BUSINESS_DATE = "20260601"


def _tick_value(
    *,
    symbol: str = _SYMBOL,
    business_date: str = _BUSINESS_DATE,
    trade_time: str,
    price: int,
    cumulative_volume: int,
    volume: int = 10,
) -> dict[str, Any]:
    return {
        "source_tr_id": "H0STCNT0",
        "market": "KRX",
        "received_at": "2026-06-01T00:00:01+00:00",
        "symbol": symbol,
        "business_date": business_date,
        "trade_time": trade_time,
        "price": price,
        "trade_type": "2",
        "trade_volume": volume,
        "vwap": Decimal(str(price)),
        "change": price - 70000,
        "change_rate": Decimal("1.23"),
        "change_sign": "2" if price >= 70000 else "5",
        "cumulative_volume": cumulative_volume,
        "trade_strength": Decimal("105.50"),
        "vi_trigger_price": price + 1000,
        "trading_halted": "0",
    }


def _message(value: dict[str, Any], *, offset: int, partition: int = 0) -> TickMessage:
    return TickMessage(value=value, topic="stock-ticks", partition=partition, offset=offset, headers={})


def _handler(db_session_factory) -> TickHandler:
    return TickHandler(
        session_factory=db_session_factory,
        tick_history_repo=TickHistoryRepository(),
        snapshot_repo=SnapshotRepository(),
        metrics_repo=Metrics5mRepository(),
        aggregator=FiveMinuteAggregator(),
    )


async def _bronze_stats(db_session_factory, symbol: str) -> tuple[int, int, int]:
    async with db_session_factory() as session:
        total = await session.scalar(
            sa.select(sa.func.count()).select_from(TickHistory).where(TickHistory.symbol == symbol)
        )
        distinct_ids = await session.scalar(
            sa.select(sa.func.count(sa.distinct(TickHistory.event_id))).where(TickHistory.symbol == symbol)
        )
        duplicated = await session.scalar(
            sa.select(sa.func.count()).select_from(
                sa.select(TickHistory.event_id)
                .where(TickHistory.symbol == symbol)
                .group_by(TickHistory.event_id)
                .having(sa.func.count() > 1)
                .subquery()
            )
        )
    return int(total or 0), int(distinct_ids or 0), int(duplicated or 0)


async def _rows_for_event_id(db_session_factory, symbol: str, event_id: str) -> int:
    async with db_session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(TickHistory)
            .where(TickHistory.symbol == symbol, TickHistory.event_id == event_id)
        )
    return int(count or 0)


async def _read_snapshot(db_session_factory, symbol: str) -> SymbolSnapshot:
    async with db_session_factory() as session:
        return (
            await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))
        ).scalar_one()


async def _read_single_bar(db_session_factory, symbol: str) -> Symbol5mMetrics:
    async with db_session_factory() as session:
        return (
            await session.execute(sa.select(Symbol5mMetrics).where(Symbol5mMetrics.symbol == symbol))
        ).scalar_one()


async def _truncate_all(db_session_factory) -> None:
    """Hard reset bronze/silver/serving between Hypothesis examples (the real handler
    COMMITS its own sessions, so per-example DB isolation cannot rely on a rolled-back
    savepoint the way the repository-level property tests do)."""
    async with db_session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "TRUNCATE bronze.tick_history, silver.symbol_5m_metrics, serving.symbol_snapshot"
            )
        )


async def test_e2e_out_of_order_plus_duplicate_idempotent_and_stale_blocked(db_session_factory):
    """Crafted, fully deterministic combined-fix proof.

    Five distinct logical ticks for ONE symbol, strictly-increasing canonical event
    time (same business_date, increasing trade_time), all inside the 09:00-09:05
    bucket. They are fed to the REAL handler in a PERMUTED arrival order in which the
    MAX-event-time tick (t4 @ 09:04:00, price 70200) arrives SECOND -- not last -- and
    one tick (t4) is republished at a DIFFERENT offset. The final arrival is an OLDER
    tick (t1 @ 09:01:00) whose price (70500) differs from the winner's, so a stale
    last-writer-wins regression would be visible as ``last_price == 70500``.
    """
    handler = _handler(db_session_factory)

    ordered = [
        _tick_value(trade_time="090000", price=70000, cumulative_volume=1_000),
        _tick_value(trade_time="090100", price=70500, cumulative_volume=2_000),
        _tick_value(trade_time="090200", price=69800, cumulative_volume=3_000),
        _tick_value(trade_time="090300", price=70100, cumulative_volume=4_000),
        _tick_value(trade_time="090400", price=70200, cumulative_volume=5_000),
    ]
    newest = ordered[4]
    newest_event_id = compute_event_id(newest)

    # Each pair is (index into `ordered`, kafka offset). The max-event-time tick
    # (idx 4 @ 09:04) arrives 2nd and sets the snapshot; every later arrival is older
    # so the conditional upsert must block it. Offset 4 republishes idx 4 at a NEW
    # offset (same payload -> same event_id) to probe idempotency. The final arrival
    # (idx 1 @ 09:01, price 70500) is stale with a DIFFERENT price -> a LWW regression
    # would surface as last_price == 70500 instead of the winner's 70200.
    arrivals: list[tuple[int, int]] = [(2, 1), (4, 2), (0, 3), (4, 4), (3, 5), (1, 6)]
    for index, offset in arrivals:
        await handler.handle(_message(ordered[index], offset=offset))

    total, distinct_ids, duplicated = await _bronze_stats(db_session_factory, _SYMBOL)
    assert total == 5, f"I1 violated: 5 distinct logical ticks (+1 duplicate) produced {total} bronze rows"
    assert distinct_ids == 5, f"expected 5 distinct event_ids, got {distinct_ids}"
    assert duplicated == 0, "I1 violated: at least one event_id has > 1 bronze row (offset leaked into identity)"
    assert await _rows_for_event_id(db_session_factory, _SYMBOL, newest_event_id) == 1, (
        "I1 violated: the republished tick added a second row for the same event_id"
    )

    snapshot = await _read_snapshot(db_session_factory, _SYMBOL)
    assert snapshot.last_price == newest["price"] == 70200, (
        f"I2 violated (stale price): snapshot.last_price={snapshot.last_price} but the max-event-time "
        f"tick price is {newest['price']}; an out-of-order / duplicate arrival overwrote serving state"
    )
    assert snapshot.last_trade_time == newest["trade_time"] == "090400", (
        f"I2 violated: snapshot.last_trade_time={snapshot.last_trade_time!r}, expected {newest['trade_time']!r}"
    )
    assert snapshot.last_event_ts == datetime(2026, 6, 1, 9, 4, tzinfo=KST), (
        f"I2 violated: snapshot.last_event_ts={snapshot.last_event_ts!r} is not the max canonical event time"
    )
    assert snapshot.cumulative_volume == newest["cumulative_volume"] == 5_000

    bar = await _read_single_bar(db_session_factory, _SYMBOL)
    assert bar.tick_count == 5, f"silver inflated: tick_count={bar.tick_count} (duplicate must not be counted)"
    assert bar.volume == 50, f"silver inflated: volume={bar.volume} (5 ticks x 10, duplicate excluded)"
    assert bar.open == 70000
    assert bar.high == 70500
    assert bar.low == 69800
    assert bar.close == 70200
    assert bar.is_final is False


_HANDLER_PROPERTY_SETTINGS = settings(COMMON_HYPOTHESIS_SETTINGS, max_examples=20)


@_HANDLER_PROPERTY_SETTINGS
@given(ticks=out_of_order_ticks())
async def test_e2e_handler_property_out_of_order_and_duplicate(
    db_session_factory,
    ticks: list[dict[str, Any]],
) -> None:
    """Property variant: for ANY permuted arrival order of same-symbol,
    strictly-increasing-event-time ticks PLUS a duplicate republish of the
    max-event-time tick, the real handler keeps bronze idempotent (one row per
    event_id) and the snapshot pinned to the max-event-time tick.

    ``out_of_order_ticks`` guarantees one symbol, one business_date, strictly
    increasing trade_time (so distinct event_ids and no event-time ties), and a
    non-identity permutation -- the shuffle is what exposes a last-writer-wins bug.
    """
    await _truncate_all(db_session_factory)

    handler = _handler(db_session_factory)
    symbol = ticks[0]["symbol"]
    newest = max(ticks, key=lambda tick: (str(tick["business_date"]), str(tick["trade_time"])))
    newest_event_id = compute_event_id(newest)

    for offset, tick in enumerate(ticks, start=1):
        await handler.handle(_message(tick, offset=offset))
    await handler.handle(_message(newest, offset=len(ticks) + 1, partition=7))

    total, distinct_ids, duplicated = await _bronze_stats(db_session_factory, symbol)
    assert total == len(ticks), f"I1 violated: {len(ticks)} logical ticks (+1 duplicate) -> {total} bronze rows"
    assert distinct_ids == len(ticks)
    assert duplicated == 0
    assert await _rows_for_event_id(db_session_factory, symbol, newest_event_id) == 1

    snapshot = await _read_snapshot(db_session_factory, symbol)
    assert snapshot.last_trade_time == newest["trade_time"], (
        f"I2 violated: snapshot kept last_trade_time={snapshot.last_trade_time!r}, "
        f"max-event-time tick is {newest['trade_time']!r} (arrival order/duplicate let a stale tick win)"
    )
    assert snapshot.last_price == newest["price"], (
        f"I2 violated: snapshot.last_price={snapshot.last_price}, expected {newest['price']}"
    )

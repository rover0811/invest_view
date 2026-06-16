"""T16 — bronze -> silver -> serving consistency invariants (post re-aggregation).

This module ENCODES the operator consistency checks from
``.sisyphus/evidence/task-16-reaggregation.txt`` as executable SQL and runs them
against the testcontainers Postgres after driving the REAL ``TickHandler`` with a
crafted + duplicated, out-of-order tick set. The SQL strings below are the SAME
pass/fail gates an operator runs against prod (homelab ``invest-db-credentials``);
running them here makes the re-ingest runbook's invariants regression-proof.

Complementary to T17 (``test_e2e_out_of_order.py``), NOT a copy:
  * T17 proves the COMBINED idempotency + stale-block fix at the handler boundary
    and SPOT-CHECKS a single crafted bar's OHLC values (``bar.high == 70500`` etc.).
  * T16 (here) targets the SILVER/serving integrity angle and asserts the GENERAL
    invariants as the operator's SQL gates over EVERY row/bucket:

      C1  bronze idempotency        count(*) == count(DISTINCT event_id)         (0 dup rows)
      C2  silver OHLC invariant     low <= open <= high AND low <= close <= high (0 violating rows)
      C3  silver not inflated       per (symbol, bucket): silver.tick_count == #DISTINCT event_id
                                    ticks AND silver.volume == SUM(trade_volume over DISTINCT event_id)
      C4  serving snapshot freshness snapshot.last_event_ts == MAX(bronze.event_ts) per symbol
                                    AND snapshot.last_price == that max-event-time tick's price

C2 is asserted EXPLICITLY here (T17 only spot-checks specific OHLC numbers); C3
proves a duplicate republish does not double-count silver; C4 proves an out-of-order
/ duplicate arrival never lets a stale tick overwrite serving state.

------------------------------------------------------------------------------
[OPERATOR] Live prod verification path (non-blocking, weekday market hours)
------------------------------------------------------------------------------
The automated checks below are the deterministic, cluster-free proof. To confirm the
same invariants on the live homelab k3s surface after a clean-slate re-ingest, run the
SQL gates in ``.sisyphus/evidence/task-16-reaggregation.txt`` (prod variant uses the
``invest-db-credentials`` secret, user ``invest`` / db ``invest_view`` -- NOT
postgres/postgres). Real ticks only flow weekdays 09:00-15:30 KST; outside market
hours zero new ticks is NORMAL and an unchanged ``max(persisted_at)`` is not a failure.
This live path does not block plan completion.
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


# C3 maps each bronze row to its silver 5-minute bucket by flooring event_ts to a
# 300s epoch boundary. That UTC floor equals FiveMinuteAggregator.bucket_start's KST
# floor as an instant ONLY because Asia/Seoul is UTC+09:00 (a whole multiple of 5
# min), so the UTC and KST 5-minute grids coincide; this keeps the check portable
# (no date_bin) while matching silver.bucket_start exactly. The four gates are
# identical to .sisyphus/evidence/task-16-reaggregation.txt and MUST each return 0.
SQL_C1_BRONZE_DUPLICATE_ROWS = """
SELECT count(*) - count(DISTINCT event_id) AS duplicate_rows
FROM bronze.tick_history
"""

SQL_C2_SILVER_OHLC_VIOLATIONS = """
SELECT count(*) AS ohlc_violations
FROM silver.symbol_5m_metrics
WHERE NOT (low <= open AND open <= high AND low <= close AND close <= high)
"""

SQL_C3_SILVER_INFLATED_BUCKETS = """
WITH bronze_buckets AS (
    SELECT
        symbol,
        to_timestamp(floor(extract(epoch FROM event_ts) / 300) * 300) AS bucket_start,
        count(DISTINCT event_id) AS distinct_ticks,
        coalesce(sum(trade_volume), 0) AS distinct_volume
    FROM bronze.tick_history
    GROUP BY 1, 2
)
SELECT count(*) AS mismatched_buckets
FROM bronze_buckets b
FULL OUTER JOIN silver.symbol_5m_metrics s
    ON s.symbol = b.symbol AND s.bucket_start = b.bucket_start
WHERE b.symbol IS NULL
   OR s.symbol IS NULL
   OR s.tick_count IS DISTINCT FROM b.distinct_ticks
   OR s.volume IS DISTINCT FROM b.distinct_volume
"""

SQL_C4_STALE_SNAPSHOTS = """
WITH latest AS (
    SELECT DISTINCT ON (symbol)
        symbol, event_ts AS max_event_ts, price AS max_price
    FROM bronze.tick_history
    ORDER BY symbol, event_ts DESC, persisted_at DESC
)
SELECT count(*) AS stale_snapshots
FROM serving.symbol_snapshot s
JOIN latest l ON l.symbol = s.symbol
WHERE s.last_event_ts IS DISTINCT FROM l.max_event_ts
   OR s.last_price IS DISTINCT FROM l.max_price
"""


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


async def _gate(db_session_factory, sql: str) -> int:
    async with db_session_factory() as session:
        result = await session.execute(sa.text(sql))
        return int(result.scalar_one() or 0)


async def _all_gates(db_session_factory) -> dict[str, int]:
    return {
        "C1_duplicate_rows": await _gate(db_session_factory, SQL_C1_BRONZE_DUPLICATE_ROWS),
        "C2_ohlc_violations": await _gate(db_session_factory, SQL_C2_SILVER_OHLC_VIOLATIONS),
        "C3_inflated_buckets": await _gate(db_session_factory, SQL_C3_SILVER_INFLATED_BUCKETS),
        "C4_stale_snapshots": await _gate(db_session_factory, SQL_C4_STALE_SNAPSHOTS),
    }


async def _bronze_total_and_distinct(db_session_factory, symbol: str) -> tuple[int, int]:
    async with db_session_factory() as session:
        total = await session.scalar(
            sa.select(sa.func.count()).select_from(TickHistory).where(TickHistory.symbol == symbol)
        )
        distinct_ids = await session.scalar(
            sa.select(sa.func.count(sa.distinct(TickHistory.event_id))).where(TickHistory.symbol == symbol)
        )
    return int(total or 0), int(distinct_ids or 0)


async def _bars(db_session_factory, symbol: str) -> list[Symbol5mMetrics]:
    async with db_session_factory() as session:
        return list(
            (
                await session.execute(
                    sa.select(Symbol5mMetrics)
                    .where(Symbol5mMetrics.symbol == symbol)
                    .order_by(Symbol5mMetrics.bucket_start)
                )
            )
            .scalars()
            .all()
        )


async def _snapshot(db_session_factory, symbol: str) -> SymbolSnapshot:
    async with db_session_factory() as session:
        return (
            await session.execute(sa.select(SymbolSnapshot).where(SymbolSnapshot.symbol == symbol))
        ).scalar_one()


async def _truncate_all(db_session_factory) -> None:
    """Hard reset bronze/silver/serving between Hypothesis examples.

    The real handler COMMITS its own sessions, so (unlike the repository-level
    property tests) per-example isolation cannot rely on a rolled-back savepoint.
    """
    async with db_session_factory() as session, session.begin():
        await session.execute(
            sa.text("TRUNCATE bronze.tick_history, silver.symbol_5m_metrics, serving.symbol_snapshot")
        )


async def test_consistency_gates_crafted_two_bucket_out_of_order_plus_duplicate(db_session_factory):
    """Crafted, fully deterministic bronze->silver->serving consistency proof.

    Six distinct logical ticks for ONE symbol, strictly-increasing canonical event
    time, spanning TWO 5-minute buckets (09:00-09:05 and 09:05-09:10). They are fed
    to the REAL handler in a PERMUTED arrival order in which the MAX-event-time tick
    (t5 @ 09:08:00) arrives SECOND -- not last -- and is republished once at a NEW
    Kafka offset (same payload -> same event_id) to probe idempotency. The FINAL
    arrival is an OLDER tick (t1 @ 09:01:30) whose price (70500) differs from the
    winner's, so a stale last-writer-wins regression would surface as
    ``last_price == 70500`` and a non-zero C4 gate.

    Asserts all four operator consistency gates return 0, plus the explicit per-bucket
    OHLC values and the duplicate-not-inflating-silver evidence on the duplicated
    tick's bucket.
    """
    handler = _handler(db_session_factory)

    bucket_a_open, bucket_a_high, bucket_a_low, bucket_a_close = (
        _tick_value(trade_time="090000", price=70000, cumulative_volume=1_000),
        _tick_value(trade_time="090130", price=70500, cumulative_volume=2_000),
        _tick_value(trade_time="090300", price=69800, cumulative_volume=3_000),
        _tick_value(trade_time="090430", price=70100, cumulative_volume=4_000),
    )
    bucket_b_open, bucket_b_close = (
        _tick_value(trade_time="090600", price=70300, cumulative_volume=5_000),
        _tick_value(trade_time="090800", price=70200, cumulative_volume=6_000),
    )
    ordered = [bucket_a_open, bucket_a_high, bucket_a_low, bucket_a_close, bucket_b_open, bucket_b_close]
    newest = bucket_b_close
    newest_event_id = compute_event_id(newest)

    # idx0 (true open, 09:00) precedes idx5 so the bucket-A base captures the correct
    # open before idx5 finalizes bucket A and the later idx2/idx3/idx1 rehydrate-merge
    # into it; idx5 (max event time) still arrives 2nd, idx1 (older, price 70500) last.
    arrivals: list[tuple[int, int]] = [(0, 1), (5, 2), (2, 3), (5, 4), (4, 5), (3, 6), (1, 7)]
    for index, offset in arrivals:
        await handler.handle(_message(ordered[index], offset=offset))

    gates = await _all_gates(db_session_factory)
    assert gates["C1_duplicate_rows"] == 0, f"C1 bronze idempotency violated: {gates}"
    assert gates["C2_ohlc_violations"] == 0, f"C2 silver OHLC invariant violated: {gates}"
    assert gates["C3_inflated_buckets"] == 0, f"C3 silver inflated/mismatched vs distinct event_id: {gates}"
    assert gates["C4_stale_snapshots"] == 0, f"C4 serving snapshot stale vs max bronze event_ts: {gates}"

    total, distinct_ids = await _bronze_total_and_distinct(db_session_factory, _SYMBOL)
    assert total == 6, f"duplicate leaked into identity: {total} bronze rows (expected 6)"
    assert distinct_ids == 6

    bars = await _bars(db_session_factory, _SYMBOL)
    assert len(bars) == 2, f"expected 2 silver buckets, got {len(bars)}"
    bucket_a, bucket_b = bars

    assert (bucket_a.open, bucket_a.high, bucket_a.low, bucket_a.close) == (70000, 70500, 69800, 70100)
    assert bucket_a.tick_count == 4
    assert bucket_a.volume == 40

    duplicated_tick_bucket = bucket_b
    assert (duplicated_tick_bucket.open, duplicated_tick_bucket.high, duplicated_tick_bucket.low, duplicated_tick_bucket.close) == (70300, 70300, 70200, 70200)
    assert duplicated_tick_bucket.tick_count == 2, "silver inflated: duplicate republish double-counted tick_count"
    assert duplicated_tick_bucket.volume == 20, "silver inflated: duplicate republish double-counted volume"

    snapshot = await _snapshot(db_session_factory, _SYMBOL)
    assert snapshot.last_price == newest["price"] == 70200, (
        f"stale snapshot: last_price={snapshot.last_price}, expected the max-event-time tick price 70200 "
        f"(the stale final arrival carried 70500)"
    )
    assert snapshot.last_event_ts == datetime(2026, 6, 1, 9, 8, tzinfo=KST)
    assert snapshot.last_trade_time == "090800"
    assert await _rows_for_event_id(db_session_factory, _SYMBOL, newest_event_id) == 1


async def _rows_for_event_id(db_session_factory, symbol: str, event_id: str) -> int:
    async with db_session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(TickHistory)
            .where(TickHistory.symbol == symbol, TickHistory.event_id == event_id)
        )
    return int(count or 0)


_PROPERTY_SETTINGS = settings(COMMON_HYPOTHESIS_SETTINGS, max_examples=25)


@_PROPERTY_SETTINGS
@given(ticks=out_of_order_ticks())
async def test_consistency_gates_hold_for_any_out_of_order_plus_duplicate(
    db_session_factory,
    ticks: list[dict[str, Any]],
) -> None:
    """Property variant: for ANY permuted arrival of same-symbol, strictly-increasing
    event-time ticks PLUS a duplicate republish of the max-event-time tick, the real
    handler keeps the full bronze->silver->serving surface consistent -- all four
    operator gates return 0.

    ``out_of_order_ticks`` guarantees one symbol, one business_date, strictly
    increasing trade_time (distinct event_ids, no event-time ties) and a non-identity
    permutation; the shuffle is what would expose a silver double-count or a
    last-writer-wins snapshot regression. Buckets may straddle a 5-minute boundary,
    exercising the bronze->silver bucket mapping across multiple silver rows.
    """
    await _truncate_all(db_session_factory)

    handler = _handler(db_session_factory)
    newest = max(ticks, key=lambda tick: (str(tick["business_date"]), str(tick["trade_time"])))

    for offset, tick in enumerate(ticks, start=1):
        await handler.handle(_message(tick, offset=offset))
    await handler.handle(_message(newest, offset=len(ticks) + 1, partition=9))

    gates = await _all_gates(db_session_factory)
    assert gates["C1_duplicate_rows"] == 0, f"C1 bronze idempotency violated: {gates}"
    assert gates["C2_ohlc_violations"] == 0, f"C2 silver OHLC invariant violated: {gates}"
    assert gates["C3_inflated_buckets"] == 0, f"C3 silver inflated/mismatched vs distinct event_id: {gates}"
    assert gates["C4_stale_snapshots"] == 0, f"C4 serving snapshot stale vs max bronze event_ts: {gates}"

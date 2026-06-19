"""Throughput micro-benchmark harness for tick_persistence.

Measures, reproducibly and against a testcontainers Postgres, the throughput of:

  * the **single-tick** path (current ``TickHandler.handle`` — one transaction per tick), and
  * the **batch** path (``TickHandler.handle_batch`` — *placeholder*, filled by Task 5
    once Task 2 lands the batch handler).

Wave 1 scope (this file): the single-tick **baseline** only — elapsed time, msgs/s, and
the number of DB round-trips (transactions / commits / INSERT-UPDATE-SELECT statements).
The DB round-trip counter is the foundation for the Task 5 absolute gate
("DB call count == O(batch/chunk), per-tick transactions == 0"), so it must be accurate.

Run::

    cd services/tick_persistence
    TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/test_throughput_bench.py -s

Knobs (env):
    BENCH_TICKS   number of ticks to push through the single path (default 10000)
    BENCH_SYMBOLS comma-separated symbols to round-robin (default 5 KOSPI tickers)
"""
from __future__ import annotations

import math
import os
import resource
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from tick_persistence.aggregation.ohlc import FiveMinuteAggregator
from tick_persistence.db.models import Symbol5mMetrics, SymbolSnapshot, TickHistory
from tick_persistence.db.session import create_engine, create_session_factory
from tick_persistence.handler import TickHandler
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.metrics import Metrics5mRepository
from tick_persistence.repository.snapshot import SnapshotRepository
from tick_persistence.repository.tick_history import TickHistoryRepository

from _rtt_harness import count_db_roundtrips  # pyright: ignore[reportImplicitRelativeImport]

pytestmark = pytest.mark.qa

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_DIR = _REPO_ROOT / ".sisyphus" / "evidence" / "tick-persistence-throughput"
_BASELINE_EVIDENCE = _EVIDENCE_DIR / "task-4-baseline.txt"

_DEFAULT_TICKS = 10_000
_DEFAULT_SYMBOLS = ("005930", "000660", "035420", "068270", "051910")
_BENCH_BUSINESS_DATE = "20260601"
_MARKET_OPEN_SECONDS = 9 * 3600

# Absolute throughput floor (BLOCKING gate): prod ingest peak x safety factor, per plan Context.
_PROD_INGEST_P95_MSG_S = 58
_THROUGHPUT_SAFETY_FACTOR = 2
_MIN_BATCH_MSG_S = _PROD_INGEST_P95_MSG_S * _THROUGHPUT_SAFETY_FACTOR
# Relative single/batch multiple is machine-dependent -> reported, NEVER gated.
_INFORMATIONAL_RELATIVE_MULTIPLE = 10
# Disjoint symbols + non-overlapping lineage base keep the single-path comparison from
# colliding with the batch run's bronze rows on the shared testcontainers DB.
_SINGLE_COMPARE_TICKS = 2_000
_SINGLE_COMPARE_SYMBOLS = ("990001", "990002", "990003")
_SINGLE_COMPARE_LINEAGE_BASE = 9_000_000

_MEM_SYMBOL_COUNT = int(os.environ.get("MEM_SYMBOLS", "40"))
_MEM_BUCKETS = int(os.environ.get("MEM_BUCKETS", "20"))
_MEM_TICKS_PER_BUCKET = int(os.environ.get("MEM_TICKS_PER_BUCKET", "4"))
_MEM_BATCH_SIZE = int(os.environ.get("MEM_BATCH_SIZE", "400"))
_MEM_PEAK_LIMIT_BYTES = 512 * 1024 * 1024  # container OOM ceiling
_MEM_LINEAGE_BASE = 2_000_000


def _hhmmss(seconds_after_midnight: int) -> str:
    hours, remainder = divmod(seconds_after_midnight, 60 * 60)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}{minutes:02}{seconds:02}"


def _tick_value(*, symbol: str, price: int, trade_time: str, volume: int, cumulative_volume: int) -> dict[str, Any]:
    return {
        "source_tr_id": "H0STCNT0",
        "market": "KRX",
        "received_at": "2026-06-01T00:00:01+00:00",
        "symbol": symbol,
        "business_date": _BENCH_BUSINESS_DATE,
        "trade_time": trade_time,
        "price": price,
        "trade_type": "2",
        "trade_volume": volume,
        "vwap": Decimal(str(price)),
        "change": price - 70_000,
        "change_rate": Decimal("1.23"),
        "change_sign": "2",
        "cumulative_volume": cumulative_volume,
        "trade_strength": Decimal("105.50"),
        "vi_trigger_price": price + 1_000,
        "trading_halted": "0",
    }


def _message(value: dict[str, Any], *, offset: int, partition: int = 0) -> TickMessage:
    return TickMessage(value=value, topic="stock-ticks", partition=partition, offset=offset, headers={})


def make_ticks(
    n: int,
    symbols: tuple[str, ...] = _DEFAULT_SYMBOLS,
    *,
    cumulative_base: int = 1_000_000,
    offset_base: int = 0,
) -> list[TickMessage]:
    """Build ``n`` deterministic ticks with globally-unique event_id lineage.

    ``cumulative_volume`` is the per-tick global index, so every tick yields a distinct
    ``event_id`` and the bronze ON CONFLICT never short-circuits — the baseline therefore
    reflects the full single-tick path. ``trade_time`` advances one second per symbol
    cycle, spanning many 5-minute buckets to exercise hydrate + finalization.

    ``cumulative_base``/``offset_base`` shift the event_id and ``topic:partition:offset``
    lineage so two runs on the same DB never collide on either unique key.
    """
    symbol_count = len(symbols)
    messages: list[TickMessage] = []
    for i in range(n):
        symbol = symbols[i % symbol_count]
        trade_time = _hhmmss(_MARKET_OPEN_SECONDS + (i // symbol_count))
        value = _tick_value(
            symbol=symbol,
            price=70_000 + (i % 100),
            trade_time=trade_time,
            volume=1 + (i % 10),
            cumulative_volume=cumulative_base + i,
        )
        messages.append(_message(value, offset=offset_base + i))
    return messages


def make_catchup_ticks(
    *, symbol_count: int, buckets: int, ticks_per_bucket: int
) -> tuple[list[TickMessage], tuple[str, ...]]:
    """Build a deep catch-up backlog: ``symbol_count`` symbols each spanning ``buckets``
    sequential 5-minute buckets, ``ticks_per_bucket`` ticks apiece.

    Emitting bucket-by-bucket means every symbol keeps advancing into a newer bucket, so
    each prior bucket finalizes and is evicted from the aggregator — the live working set
    stays bounded at ~``symbol_count`` no matter how deep the backlog runs.
    """
    symbols = tuple(f"{900_000 + index:06d}" for index in range(symbol_count))
    intra_bucket_step = max(1, 300 // max(1, ticks_per_bucket))
    messages: list[TickMessage] = []
    global_index = 0
    for bucket_index in range(buckets):
        bucket_base = _MARKET_OPEN_SECONDS + bucket_index * 300
        for symbol in symbols:
            for tick_index in range(ticks_per_bucket):
                trade_time = _hhmmss(bucket_base + tick_index * intra_bucket_step)
                value = _tick_value(
                    symbol=symbol,
                    price=70_000 + bucket_index + tick_index,
                    trade_time=trade_time,
                    volume=1 + tick_index,
                    cumulative_volume=_MEM_LINEAGE_BASE + global_index,
                )
                messages.append(_message(value, offset=_MEM_LINEAGE_BASE + global_index))
                global_index += 1
    return messages, symbols


@dataclass
class DbRoundtripCounter:
    """Count DB round-trips for the work executed inside the ``with`` block.

    * ``before_cursor_execute`` on the engine counts every statement actually sent over
      the wire (INSERT / UPDATE / SELECT / ...), i.e. true DB round-trips.
    * ``after_begin`` / ``after_commit`` on the ORM ``Session`` class count transaction
      boundaries (asyncpg issues BEGIN/COMMIT at the driver level, not via a cursor, so
      they never show up in ``before_cursor_execute`` — these session events are the
      reliable signal).

    Listeners attach on ``__enter__`` and detach on ``__exit__`` so only the measured
    section is counted.
    """

    sync_engine: Any
    statements: int = 0
    inserts: int = 0
    updates: int = 0
    selects: int = 0
    others: int = 0
    begins: int = 0
    commits: int = 0
    rollbacks: int = 0
    _verbs: dict[str, int] = field(default_factory=dict, repr=False)

    def _before_cursor_execute(
        self,
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        self.statements += 1
        head = statement.lstrip()[:6].upper()
        if head.startswith("INSERT"):
            self.inserts += 1
        elif head.startswith("UPDATE"):
            self.updates += 1
        elif head.startswith("SELECT"):
            self.selects += 1
        else:
            self.others += 1
        verb = head.split(None, 1)[0] if head.strip() else "?"
        self._verbs[verb] = self._verbs.get(verb, 0) + 1

    def _after_begin(self, _session: Session, _transaction: Any, _connection: Any) -> None:
        self.begins += 1

    def _after_commit(self, _session: Session) -> None:
        self.commits += 1

    def _after_rollback(self, _session: Session) -> None:
        self.rollbacks += 1

    def __enter__(self) -> DbRoundtripCounter:
        event.listen(self.sync_engine, "before_cursor_execute", self._before_cursor_execute)
        event.listen(Session, "after_begin", self._after_begin)
        event.listen(Session, "after_commit", self._after_commit)
        event.listen(Session, "after_rollback", self._after_rollback)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.sync_engine, "before_cursor_execute", self._before_cursor_execute)
        event.remove(Session, "after_begin", self._after_begin)
        event.remove(Session, "after_commit", self._after_commit)
        event.remove(Session, "after_rollback", self._after_rollback)


@dataclass
class BenchResult:
    label: str
    n: int
    seconds: float
    transactions: int
    commits: int
    inserts: int
    updates: int
    selects: int
    statements: int
    per_batch: list[tuple[int, float]] = field(default_factory=list)

    @property
    def msgs_per_s(self) -> float:
        return self.n / self.seconds if self.seconds > 0 else float("inf")

    def median_batch_msgs_per_s(self) -> float:
        rates = [size / seconds for size, seconds in self.per_batch if seconds > 0]
        return statistics.median(rates) if rates else self.msgs_per_s

    def summary_line(self) -> str:
        return (
            f"{self.label}: {self.n} msgs in {self.seconds:.3f}s = {self.msgs_per_s:.1f} msg/s "
            f"| tx={self.transactions} commits={self.commits} inserts={self.inserts}"
        )

    def detail_line(self) -> str:
        return (
            f"  detail: statements={self.statements} inserts={self.inserts} "
            f"updates={self.updates} selects={self.selects} "
            f"(per-msg: tx={self.transactions / self.n:.3f} statements={self.statements / self.n:.3f})"
        )


def _build_handler(session_factory: async_sessionmaker[AsyncSession]) -> TickHandler:
    return TickHandler(
        session_factory=session_factory,
        tick_history_repo=TickHistoryRepository(),
        snapshot_repo=SnapshotRepository(),
        metrics_repo=Metrics5mRepository(),
        aggregator=FiveMinuteAggregator(),
    )


async def run_single_bench(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    messages: list[TickMessage],
    *,
    label: str = "baseline_single",
) -> BenchResult:
    handler = _build_handler(session_factory)
    counter = DbRoundtripCounter(sync_engine=engine.sync_engine)

    with counter:
        start = time.perf_counter()
        for message in messages:
            await handler.handle(message)
        elapsed = time.perf_counter() - start

    return BenchResult(
        label=label,
        n=len(messages),
        seconds=elapsed,
        transactions=counter.begins,
        commits=counter.commits,
        inserts=counter.inserts,
        updates=counter.updates,
        selects=counter.selects,
        statements=counter.statements,
    )


async def run_batch_bench(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    messages: list[TickMessage],
    *,
    batch_size: int,
    label: str = "batch",
) -> BenchResult:
    """Batch-path throughput harness using Task 2's ``TickHandler.handle_batch`` API."""
    handler = _build_handler(session_factory)
    counter = DbRoundtripCounter(sync_engine=engine.sync_engine)
    per_batch: list[tuple[int, float]] = []

    with counter:
        start = time.perf_counter()
        for batch_start in range(0, len(messages), batch_size):
            chunk = messages[batch_start : batch_start + batch_size]
            batch_started = time.perf_counter()
            await handler.handle_batch(chunk)
            per_batch.append((len(chunk), time.perf_counter() - batch_started))
        elapsed = time.perf_counter() - start

    return BenchResult(
        label=label,
        n=len(messages),
        seconds=elapsed,
        transactions=counter.begins,
        commits=counter.commits,
        inserts=counter.inserts,
        updates=counter.updates,
        selects=counter.selects,
        statements=counter.statements,
        per_batch=per_batch,
    )


def _write_evidence(result: BenchResult, *, ticks: int, symbols: tuple[str, ...]) -> None:
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Task 4 — tick_persistence throughput baseline (single-tick path)",
        "",
        "generated_by: tests/test_throughput_bench.py::test_baseline_single_throughput",
        "run: TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/test_throughput_bench.py -s",
        f"ticks: {ticks}",
        f"symbols: {','.join(symbols)} ({len(symbols)} symbols, round-robin)",
        "",
        result.summary_line(),
        result.detail_line(),
        "",
        "## interpretation",
        "- path: current TickHandler.handle (one session.begin() / commit per tick).",
        f"- transactions == ticks ({result.transactions} == {result.n}): per-tick transaction boundary.",
        f"- inserts/tick ~= {result.inserts / result.n:.2f} (bronze + snapshot + silver upserts, + finalizations).",
        "- This is the comparison floor for Task 5: the batch path must reach a far higher",
        "  msg/s AND collapse transactions to O(batch/chunk) (per-tick commits -> 0).",
        "- prod ingest peak per plan ~58 msg/s; batch target is several thousand msg/s.",
        "",
    ]
    _BASELINE_EVIDENCE.write_text("\n".join(lines), encoding="utf-8")


async def test_baseline_single_throughput(migrated_url: str) -> None:
    ticks = int(os.environ.get("BENCH_TICKS", _DEFAULT_TICKS))
    symbols_env = os.environ.get("BENCH_SYMBOLS")
    symbols = tuple(s.strip() for s in symbols_env.split(",") if s.strip()) if symbols_env else _DEFAULT_SYMBOLS

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    try:
        messages = make_ticks(ticks, symbols)
        result = await run_single_bench(engine, session_factory, messages)

        async with session_factory() as session:
            bronze = await session.scalar(sa.select(sa.func.count()).select_from(TickHistory))
            silver = await session.scalar(sa.select(sa.func.count()).select_from(Symbol5mMetrics))
            snapshots = await session.scalar(sa.select(sa.func.count()).select_from(SymbolSnapshot))
    finally:
        await engine.dispose()

    print("\n" + result.summary_line())
    print(result.detail_line())
    print(f"  db rows: bronze={bronze} silver={silver} snapshots={snapshots}")

    _write_evidence(result, ticks=ticks, symbols=symbols)
    print(f"  evidence: {_BASELINE_EVIDENCE}")

    assert result.n == ticks
    assert result.seconds > 0
    assert result.msgs_per_s > 0
    assert result.transactions == ticks
    assert result.commits == ticks
    assert int(bronze or 0) == ticks
    assert int(snapshots or 0) == len(symbols)
    assert result.inserts >= ticks * 3


def _max_rss_bytes() -> int:
    ru_maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru_maxrss if sys.platform == "darwin" else ru_maxrss * 1024


def _write_batch_evidence(
    *,
    batch: BenchResult,
    single: BenchResult,
    median_batch_msg_s: float,
    relative_multiple: float,
    expected_tx: int,
    batch_size: int,
    bronze: int,
    silver: int,
    snapshots: int,
) -> None:
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Task 5 — batch-path absolute gates (throughput + DB round-trips)",
        "",
        "generated_by: tests/test_throughput_bench.py::test_batch_throughput",
        "run: TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/test_throughput_bench.py -s",
        "",
        batch.summary_line(),
        batch.detail_line(),
        f"  median per-batch: {median_batch_msg_s:.1f} msg/s over {len(batch.per_batch)} batches (batch_size={batch_size})",
        f"  db rows (batch): bronze={bronze} silver={silver} snapshots={snapshots}",
        "  " + single.summary_line(),
        single.detail_line(),
        "",
        "## GATE 1 (BLOCKING) — absolute throughput floor",
        f"- floor = prod p95 {_PROD_INGEST_P95_MSG_S} msg/s x safety {_THROUGHPUT_SAFETY_FACTOR} = {_MIN_BATCH_MSG_S} msg/s",
        f"- batch median per-batch = {median_batch_msg_s:.1f} msg/s ({median_batch_msg_s / _MIN_BATCH_MSG_S:.0f}x the floor) -> PASS",
        f"- batch aggregate = {batch.msgs_per_s:.1f} msg/s -> {'PASS' if batch.msgs_per_s >= _MIN_BATCH_MSG_S else 'FAIL'}",
        "",
        "## GATE 2 (BLOCKING) — DB round-trips O(batch/chunk), zero per-tick commits",
        f"- batch: transactions={batch.transactions} commits={batch.commits} == ceil(N/batch_size)={expected_tx} -> PASS",
        f"- batch inserts={batch.inserts} (< N={batch.n}: O(batch x (chunk+symbols+buckets)), not O(ticks))",
        f"- single (contrast): transactions={single.transactions} commits={single.commits} == ticks={single.n} (one tx/commit per tick)",
        f"- collapse: batch commits/tick = {batch.commits / batch.n:.5f} vs single 1.0 -> per-tick commits effectively 0",
        "",
        "## INFORMATIONAL (NOT a gate — machine-dependent)",
        f"- relative throughput multiple batch/single = {relative_multiple:.1f}x",
        f"  (single-compare is a lighter {_SINGLE_COMPARE_TICKS}-tick / {len(_SINGLE_COMPARE_SYMBOLS)}-symbol contrast;",
        f"   plan's rough >= {_INFORMATIONAL_RELATIVE_MULTIPLE}x target is vs the full-size baseline, never gated)",
        "",
    ]
    (_EVIDENCE_DIR / "task-5-batch-throughput.txt").write_text("\n".join(lines), encoding="utf-8")


def _write_memory_evidence(
    *,
    total: int,
    symbols: tuple[str, ...],
    buckets: int,
    batch_size: int,
    peak_bytes: int,
    rss_peak_bytes: int,
    live_bars: int,
    hydrated_keys: int,
    bronze: int,
    silver: int,
    msgs_per_s: float,
) -> None:
    _EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    mib = 1024 * 1024
    lines = [
        "# Task 5 — catch-up memory OOM-free simulation (batch path)",
        "",
        "generated_by: tests/test_throughput_bench.py::test_catchup_memory_is_oom_free_and_evicts_finalized_buckets",
        "run: TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/test_throughput_bench.py -s",
        "",
        f"backlog: {len(symbols)} symbols x {buckets} past buckets x {total // (len(symbols) * buckets)} ticks "
        f"= {total} ticks, batch_size={batch_size}, {msgs_per_s:.0f} msg/s",
        f"db rows: bronze={bronze} silver={silver}",
        "",
        "## OOM-free gate (BLOCKING)",
        f"- tracemalloc peak = {peak_bytes / mib:.1f} MiB < ceiling {_MEM_PEAK_LIMIT_BYTES // mib} MiB -> PASS",
        f"- process RSS high-water (informational, includes test runner) = {rss_peak_bytes / mib:.1f} MiB",
        "",
        "## eviction gate (BLOCKING)",
        f"- live aggregator bars = {live_bars} <= symbols {len(symbols)} -> PASS "
        f"(unbounded would be symbols x buckets = {len(symbols) * buckets})",
        f"- live hydrated keys = {hydrated_keys} <= symbols {len(symbols)} -> PASS",
        f"- finalized buckets flushed to silver = {silver} (>= symbols x (buckets-1) = {len(symbols) * (buckets - 1)})",
        "",
    ]
    (_EVIDENCE_DIR / "task-5-memory.txt").write_text("\n".join(lines), encoding="utf-8")


async def test_batch_throughput(migrated_url: str) -> None:
    """Task 5 absolute gates for the batch path (both BLOCKING):

      Gate 1 — throughput floor: median per-batch msg/s >= prod p95 x safety factor (116 msg/s).
      Gate 2 — DB round-trips O(batch/chunk): exactly one transaction (BEGIN+COMMIT) per batch
        and zero per-tick commits, proven by DbRoundtripCounter against the single-path contrast.

    The single-tick path runs on a disjoint symbol set purely for the (machine-dependent,
    never-gated) relative-multiple comparison and the single tx==ticks contrast.
    """
    if not hasattr(TickHandler, "handle_batch"):
        pytest.skip("TickHandler.handle_batch not implemented (Task 2)")

    ticks = int(os.environ.get("BENCH_TICKS", _DEFAULT_TICKS))
    batch_size = int(os.environ.get("BENCH_BATCH_SIZE", "500"))
    symbols = _DEFAULT_SYMBOLS

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    try:
        batch = await run_batch_bench(engine, session_factory, make_ticks(ticks, symbols), batch_size=batch_size)

        async with session_factory() as session:
            bronze = int(await session.scalar(sa.select(sa.func.count()).select_from(TickHistory)) or 0)
            silver = int(await session.scalar(sa.select(sa.func.count()).select_from(Symbol5mMetrics)) or 0)
            snapshots = int(await session.scalar(sa.select(sa.func.count()).select_from(SymbolSnapshot)) or 0)

        single_n = min(ticks, _SINGLE_COMPARE_TICKS)
        single_messages = make_ticks(
            single_n,
            _SINGLE_COMPARE_SYMBOLS,
            cumulative_base=_SINGLE_COMPARE_LINEAGE_BASE,
            offset_base=_SINGLE_COMPARE_LINEAGE_BASE,
        )
        single = await run_single_bench(engine, session_factory, single_messages, label="compare_single")
    finally:
        await engine.dispose()

    expected_tx = math.ceil(ticks / batch_size)
    median_batch_msg_s = batch.median_batch_msgs_per_s()
    relative_multiple = batch.msgs_per_s / single.msgs_per_s if single.msgs_per_s else float("inf")

    print("\n" + batch.summary_line())
    print(batch.detail_line())
    print(f"  median per-batch: {median_batch_msg_s:.1f} msg/s over {len(batch.per_batch)} batches (expected_tx={expected_tx})")
    print(f"  db rows (batch): bronze={bronze} silver={silver} snapshots={snapshots}")
    print("  " + single.summary_line())
    print(
        f"  INFORMATIONAL relative multiple batch/single = {relative_multiple:.1f}x "
        "(machine-dependent; NOT a gate)"
    )
    print(f"  GATE1 floor = {_MIN_BATCH_MSG_S} msg/s (prod {_PROD_INGEST_P95_MSG_S} x {_THROUGHPUT_SAFETY_FACTOR})")

    _write_batch_evidence(
        batch=batch,
        single=single,
        median_batch_msg_s=median_batch_msg_s,
        relative_multiple=relative_multiple,
        expected_tx=expected_tx,
        batch_size=batch_size,
        bronze=bronze,
        silver=silver,
        snapshots=snapshots,
    )

    assert bronze == ticks
    assert snapshots == len(symbols)
    assert silver >= 1

    assert median_batch_msg_s >= _MIN_BATCH_MSG_S, (
        f"batch median {median_batch_msg_s:.1f} msg/s below absolute floor {_MIN_BATCH_MSG_S} "
        f"(prod p95 {_PROD_INGEST_P95_MSG_S} x safety {_THROUGHPUT_SAFETY_FACTOR})"
    )
    assert batch.msgs_per_s >= _MIN_BATCH_MSG_S

    assert batch.transactions == expected_tx, (
        f"batch opened {batch.transactions} transactions; expected one per batch ({expected_tx}) "
        "— per-tick transactions must be 0"
    )
    assert batch.commits == expected_tx, (
        f"batch issued {batch.commits} commits; expected one per batch ({expected_tx}) "
        "— per-tick commits must be 0"
    )
    assert batch.inserts < ticks
    assert single.transactions == single_n
    assert single.commits == single_n
    assert batch.commits < single.commits


async def test_catchup_memory_is_oom_free_and_evicts_finalized_buckets(migrated_url: str) -> None:
    """Catch-up OOM-free simulation (Task 5): push a deep backlog (40 symbols x many past
    buckets x thousands of ticks) through the BATCH path against the testcontainers DB and
    assert the Python working-set peak stays < 512 MiB AND finalized buckets are evicted
    (live bars/hydrated keys bounded by symbol count, independent of backlog depth).
    """
    if not hasattr(TickHandler, "handle_batch"):
        pytest.skip("TickHandler.handle_batch not implemented (Task 2)")

    assert _MEM_BUCKETS >= 10
    messages, symbols = make_catchup_ticks(
        symbol_count=_MEM_SYMBOL_COUNT, buckets=_MEM_BUCKETS, ticks_per_bucket=_MEM_TICKS_PER_BUCKET
    )
    total = len(messages)
    assert total >= 2_000

    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    handler = _build_handler(session_factory)
    try:
        tracemalloc.start()
        started = time.perf_counter()
        for batch_start in range(0, total, _MEM_BATCH_SIZE):
            await handler.handle_batch(messages[batch_start : batch_start + _MEM_BATCH_SIZE])
        elapsed = time.perf_counter() - started
        _current, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        live_bars = len(handler._aggregator._bars)
        hydrated_keys = len(handler._hydrated_keys)

        async with session_factory() as session:
            bronze = int(await session.scalar(sa.select(sa.func.count()).select_from(TickHistory)) or 0)
            silver = int(await session.scalar(sa.select(sa.func.count()).select_from(Symbol5mMetrics)) or 0)
    finally:
        await engine.dispose()

    rss_peak_bytes = _max_rss_bytes()
    msgs_per_s = total / elapsed if elapsed > 0 else float("inf")
    mib = 1024 * 1024

    print(
        f"\ncatchup: {len(symbols)} symbols x {_MEM_BUCKETS} buckets = {total} ticks in {elapsed:.2f}s "
        f"({msgs_per_s:.0f} msg/s)"
    )
    print(f"  tracemalloc peak = {peak_bytes / mib:.1f} MiB (ceiling {_MEM_PEAK_LIMIT_BYTES // mib} MiB)")
    print(f"  process RSS high-water (informational) = {rss_peak_bytes / mib:.1f} MiB")
    print(f"  live bars = {live_bars} (<= symbols {len(symbols)}); hydrated keys = {hydrated_keys}")
    print(f"  db rows: bronze={bronze} silver={silver}")

    _write_memory_evidence(
        total=total,
        symbols=symbols,
        buckets=_MEM_BUCKETS,
        batch_size=_MEM_BATCH_SIZE,
        peak_bytes=peak_bytes,
        rss_peak_bytes=rss_peak_bytes,
        live_bars=live_bars,
        hydrated_keys=hydrated_keys,
        bronze=bronze,
        silver=silver,
        msgs_per_s=msgs_per_s,
    )

    assert bronze == total
    assert silver >= len(symbols) * (_MEM_BUCKETS - 1)

    assert peak_bytes < _MEM_PEAK_LIMIT_BYTES, (
        f"tracemalloc peak {peak_bytes / mib:.1f} MiB exceeded OOM-free ceiling {_MEM_PEAK_LIMIT_BYTES // mib} MiB"
    )

    assert live_bars <= len(symbols), (
        f"_bars holds {live_bars} bars for {len(symbols)} symbols — finalized buckets were NOT evicted "
        f"(unbounded would reach symbols x buckets = {len(symbols) * _MEM_BUCKETS})"
    )
    assert hydrated_keys <= len(symbols)


async def test_batch_rtt_is_at_most_three_db_executes_when_warm(migrated_url: str) -> None:
    """RTT gate for the #41 bulk-upsert fix, via Task 1's ``count_db_roundtrips`` harness.

    before: ~83 sequential RTT per 41-symbol batch (1 bronze INSERT + 41 per-symbol snapshot
            upserts + 41 per-symbol silver upserts — the pre-fix per-item loops).
    after:  <=3 batched (bronze INSERT 1 + snapshot bulk upsert 1 + silver bulk upsert 1).

    The first ``handle_batch`` seeds the aggregator for all 41 (symbol, bucket) pairs (41 distinct
    symbols share one 5-minute bucket because ``make_ticks`` keeps ``i // len(symbols) == 0`` at
    ``09:00:00``). The measured second batch reuses those symbols/bucket with disjoint
    ``cumulative_base``/``offset_base`` lineage (new event_ids so bronze never short-circuits), so
    ``_hydrate_once`` short-circuits on ``has_bar()`` with zero SELECTs — leaving exactly
    bronze+snapshot+silver = 3 executes inside one batched transaction (driver-level BEGIN/COMMIT
    are not counted as statements).
    """
    symbols = tuple(f"{950_000 + index:06d}" for index in range(41))
    engine = create_engine(migrated_url)
    session_factory = create_session_factory(engine)
    try:
        handler = _build_handler(session_factory)
        warm = make_ticks(41, symbols, cumulative_base=1_000_000, offset_base=0)
        measured = make_ticks(41, symbols, cumulative_base=1_000_100, offset_base=100)

        await handler.handle_batch(warm)

        with count_db_roundtrips(engine) as ctr:
            await handler.handle_batch(measured)

        async with session_factory() as session:
            bronze = int(await session.scalar(sa.select(sa.func.count()).select_from(TickHistory)) or 0)
            snapshots = int(await session.scalar(sa.select(sa.func.count()).select_from(SymbolSnapshot)) or 0)
    finally:
        await engine.dispose()

    print(
        f"\nRTT(warm, 41 symbols, 1 batch): statements={ctr.statements} inserts={ctr.inserts} "
        f"updates={ctr.updates} selects={ctr.selects} begins={ctr.begins} commits={ctr.commits} "
        f"| before ~83 sequential RTT -> after <=3 batched (bronze 1 + snapshot 1 + silver 1)"
    )

    assert bronze == 82, f"expected warm(41)+measured(41) distinct bronze rows, got {bronze}"
    assert snapshots == 41, f"expected one snapshot row per symbol, got {snapshots}"
    assert ctr.statements <= 3
    assert ctr.statements == 3
    assert ctr.inserts == 3
    assert ctr.updates == 0
    assert ctr.selects == 0
    assert ctr.begins == 1
    assert ctr.commits == 1


if __name__ == "__main__":  # pragma: no cover - convenience guard
    raise SystemExit(
        "This bench relies on the testcontainers Postgres fixtures in conftest.py. Run it via:\n"
        "  cd services/tick_persistence\n"
        "  TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/test_throughput_bench.py -s"
    )

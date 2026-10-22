"""Cluster-side replay driver.

The pure parts live here: the DAILY move id, the resumable state file and
the mapping from processing wall time (DB timestamps) to the replay clock
(event time of the step that caused the row). ``run_replay`` and
``export_replay`` live in ``replay.runner`` with the cluster I/O.

Runners (document processor, signal worker) should run with
``AI_SIGNAL_LLM_CACHE=postgres`` during a replay so identical prompts are
answered from ``gold.llm_cache``. ``CLEANUP_STATEMENTS`` never touches that
table, so the cache survives a re-run.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import uuid
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from ai_signal.replay import bars as bars_mod
from ai_signal.replay.bars import KST, Bar
from ai_signal.replay.cluster import Observation, observation_from_dict
from ai_signal.replay.export import build_export

# Same namespace and name layout as stream_detection_java move/DailyMoveRule.java.
_NAMESPACE_DNS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
STATE_DIR = Path("/tmp/replay-state")
LLM_CALL_CAP = 40
REPO_ROOT = Path(__file__).resolve().parents[5]
EXPERIMENTS_DIR = REPO_ROOT / "experiments" / "return_decomposition"
EXPORT_DIR = REPO_ROOT / "frontend" / "public" / "replay"
DEFAULT_DSN = "postgresql://postgres:postgres@localhost:15432/invest_view"
CANDIDATE_START = time(15, 30)
CANDIDATE_END = time(20, 0)
# Synthetic E2E move left over from the earlier 2026-10-20 run.
STALE_MOVE_DAY = date(2026, 10, 20)


def daily_move_id(symbol: str, day: date) -> str:
    return str(uuid.uuid5(_NAMESPACE_DNS, f"{symbol}|{day.isoformat()}|DAILY"))


@dataclass(frozen=True)
class Step:
    """One replay step: events at ``replay_t`` were produced in [wall_start, wall_end]."""

    replay_t: str
    wall_start: str
    wall_end: str


@dataclass
class ReplayState:
    symbol: str
    day: str
    next_event: int = 0
    llm_calls: int = 0
    steps: list[Step] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    baseline_signals: list[int] = field(default_factory=list)
    baseline_judgements: list[str] = field(default_factory=list)

    def record(
        self, step: Step, llm_calls: int, observation: Observation | None = None
    ) -> None:
        self.steps.append(step)
        self.next_event += 1
        self.llm_calls += llm_calls
        if observation is not None:
            self.observations.append(observation)

    @property
    def capped(self) -> bool:
        return self.llm_calls >= LLM_CALL_CAP


def state_path(symbol: str, day: date, base: Path = STATE_DIR) -> Path:
    return base / f"{symbol}-{day.isoformat()}.json"


def load_state(symbol: str, day: date, base: Path = STATE_DIR) -> ReplayState:
    path = state_path(symbol, day, base)
    if not path.exists():
        return ReplayState(symbol=symbol, day=day.isoformat())
    raw = json.loads(path.read_text(encoding="utf-8"))
    steps = [Step(**s) for s in raw.pop("steps", [])]
    observations = [observation_from_dict(o) for o in raw.pop("observations", [])]
    return ReplayState(**raw, steps=steps, observations=observations)


def save_state(state: ReplayState, base: Path = STATE_DIR) -> Path:
    path = state_path(state.symbol, date.fromisoformat(state.day), base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def to_replay_clock(created_at: datetime, steps: list[Step]) -> datetime | None:
    """Replay time of the latest step that started at or before ``created_at``.

    Rows written while step N was being processed (or before step N+1
    started) belong to step N. Rows older than the first step return None.
    """
    starts = [datetime.fromisoformat(s.wall_start) for s in steps]
    index = bisect_right(starts, created_at) - 1
    if index < 0:
        return None
    return datetime.fromisoformat(steps[index].replay_t)


def candidate_range(prev_day: date, day: date) -> tuple[datetime, datetime]:
    """Candidate article range: previous trading day 15:30 to 20:00 of ``day`` (KST)."""
    return (
        datetime.combine(prev_day, CANDIDATE_START, tzinfo=KST),
        datetime.combine(day, CANDIDATE_END, tzinfo=KST),
    )


def news_in_range(
    rows: Iterable[Mapping[str, str]], day: date, start: datetime, end: datetime
) -> list[dict[str, str]]:
    """KIS news-title rows of ``day`` (``time`` = HHMMSS) whose publish time is in [start, end]."""
    selected: list[tuple[datetime, dict[str, str]]] = []
    for row in rows:
        published = datetime.strptime(
            day.strftime("%Y%m%d") + str(row["time"]).zfill(6), "%Y%m%d%H%M%S"
        )
        published = published.replace(tzinfo=KST)
        if start <= published <= end:
            selected.append(
                (
                    published,
                    {
                        "srno": str(row["srno"]),
                        "title": str(row["title"]),
                        "provider": str(row["provider"]),
                        "published_at": published.isoformat(timespec="seconds"),
                    },
                )
            )
    selected.sort(key=lambda item: item[0])
    return [item for _, item in selected]


def bar_to_json(bar: Bar) -> dict[str, Any]:
    return {
        "t": bar.t.isoformat(timespec="seconds"),
        "o": bar.open,
        "h": bar.high,
        "l": bar.low,
        "c": bar.close,
        "v": bar.volume,
    }


_STALE_MOVES = """
    SELECT m.move_id FROM gold.move m
    JOIN reference.entity_symbol s ON s.entity_id = m.symbol_entity_id
    WHERE s.ticker = $1
      AND (m.move_id = $3::uuid OR (m.window_end AT TIME ZONE 'Asia/Seoul')::date = $2)
"""
_HISTORY = "SELECT move_id FROM serving.symbol_signal_history WHERE symbol = $1"

# Children before parents (serving.symbol_signal -> gold.move has no cascade).
CLEANUP_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "serving.symbol_signal_cause_source",
        "DELETE FROM serving.symbol_signal_cause_source WHERE symbol = $1",
    ),
    (
        "serving.symbol_signal_cause",
        "DELETE FROM serving.symbol_signal_cause WHERE symbol = $1",
    ),
    ("serving.symbol_signal", "DELETE FROM serving.symbol_signal WHERE symbol = $1"),
    (
        "serving.symbol_signal_history_cause_source",
        f"DELETE FROM serving.symbol_signal_history_cause_source WHERE move_id IN ({_HISTORY})",
    ),
    (
        "serving.symbol_signal_history_cause",
        f"DELETE FROM serving.symbol_signal_history_cause WHERE move_id IN ({_HISTORY})",
    ),
    (
        "serving.symbol_signal_history",
        "DELETE FROM serving.symbol_signal_history WHERE symbol = $1",
    ),
    (
        "gold.signal_cause_source",
        f"DELETE FROM gold.signal_cause_source WHERE move_id IN ({_STALE_MOVES})",
    ),
    (
        "gold.signal_cause",
        f"DELETE FROM gold.signal_cause WHERE move_id IN ({_STALE_MOVES})",
    ),
    (
        "gold.signal_decision",
        f"DELETE FROM gold.signal_decision WHERE move_id IN ({_STALE_MOVES})",
    ),
    ("gold.signal", f"DELETE FROM gold.signal WHERE move_id IN ({_STALE_MOVES})"),
    (
        "gold.signal_retry",
        f"DELETE FROM gold.signal_retry WHERE move_id IN ({_STALE_MOVES})",
    ),
    ("gold.move", f"DELETE FROM gold.move WHERE move_id IN ({_STALE_MOVES})"),
    ("outbox.events", "DELETE FROM outbox.events WHERE aggregateid = $1"),
)


def cleanup_args(table: str, symbol: str, stale_day: date) -> tuple[object, ...]:
    if "$3" in dict(CLEANUP_STATEMENTS)[table]:
        return (symbol, stale_day, daily_move_id(symbol, stale_day))
    return (symbol,)


def _deleted_count(status: str) -> int:
    return int(status.rsplit(" ", 1)[-1])


async def _prepare_db(
    symbol: str, day: date, stale_day: date, dsn: str
) -> tuple[dict[str, int], str, date, int]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        counts: dict[str, int] = {}
        async with conn.transaction():
            for table, sql in CLEANUP_STATEMENTS:
                status = await conn.execute(
                    sql, *cleanup_args(table, symbol, stale_day)
                )
                counts[table] = _deleted_count(status)
        name = await conn.fetchval(
            "SELECT e.name FROM reference.entity e JOIN reference.entity_symbol s USING (entity_id) "
            "WHERE s.ticker = $1",
            symbol,
        )
        prev = await conn.fetchrow(
            "SELECT trade_date, close FROM silver.symbol_daily_ohlc "
            "WHERE symbol = $1 AND interval = 'd' AND trade_date < $2 ORDER BY trade_date DESC LIMIT 1",
            symbol,
            day,
        )
    finally:
        await conn.close()
    if name is None or prev is None:
        raise RuntimeError(
            f"missing reference name or previous close for {symbol} before {day.isoformat()}"
        )
    return counts, str(name), prev["trade_date"], int(prev["close"])


def _experiments_kis() -> tuple[Any, Any]:
    if str(EXPERIMENTS_DIR) not in sys.path:
        sys.path.insert(0, str(EXPERIMENTS_DIR))
    kis = importlib.import_module("kis")
    data = importlib.import_module("data")
    return kis.KisClient(), data.fetch_news_titles


def inputs_path(symbol: str, day: date, base: Path = STATE_DIR) -> Path:
    return base / f"{symbol}-{day.isoformat()}-inputs.json"


def prepare_replay(symbol: str, day: date, stale_day: date = STALE_MOVE_DAY) -> None:
    dsn = os.environ.get("REPLAY_PG_DSN", DEFAULT_DSN)
    counts, name, prev_day, prev_close = asyncio.run(
        _prepare_db(symbol, day, stale_day, dsn)
    )
    for table, count in counts.items():
        print(f"deleted {count:>4} {table}")

    client, fetch_news_titles = _experiments_kis()
    day_bars = bars_mod.fetch_minute_bars(client, symbol, day)
    start, end = candidate_range(prev_day, day)
    news: list[dict[str, str]] = []
    for news_day in (prev_day, day):
        frame = fetch_news_titles(client, symbol, news_day)
        news += news_in_range(frame.to_dict("records"), news_day, start, end)
    news.sort(key=lambda n: n["published_at"])

    path = inputs_path(symbol, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs = {
        "symbol": symbol,
        "name": name,
        "date": day.isoformat(),
        "prev_day": prev_day.isoformat(),
        "prev_close": prev_close,
        "candidate_range": [start.isoformat(), end.isoformat()],
        "bars": [bar_to_json(b) for b in day_bars],
        "news": news,
    }
    path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")

    export = build_export(
        symbol=symbol,
        name=name,
        day=day,
        prev_close=prev_close,
        bars=day_bars,
        states=[],
        documents=[],
        signals=[],
        log=[],
    )
    export_path = EXPORT_DIR / f"{symbol}-{day.isoformat()}.json"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(
        json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"prev_close {prev_close} ({prev_day.isoformat()}), bars {len(day_bars)}, news in range {len(news)}"
    )
    print(f"wrote {path}")
    print(f"wrote {export_path}")


"""Cluster I/O for the replay: publish steps, wait for idle, observe and export.

Pure helpers (record building, idle detection, observation to states/log)
are kept free of I/O so they can be unit tested with fakes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ai_signal.replay.bars import Bar
from ai_signal.replay.export import (
    CauseRow,
    DocumentRow,
    LogEntry,
    SignalRow,
    StateRow,
    particle,
)

NEWS_SOURCE = "kis_news"
TICKS_TOPIC = "stock-ticks"
NEWS_TOPIC = "news-raw"
STATE_LABELS = {"POSITIVE": "긍정", "NEGATIVE": "부정", "NEUTRAL": "중립"}


@dataclass(frozen=True)
class MoveSnapshot:
    version: int
    price_state: str
    window_end: str
    return_pct: float
    signal_status: str | None = None
    decomposition: dict[str, object] | None = None


@dataclass(frozen=True)
class NewSignal:
    version: int
    status: str
    decided_by: str | None


@dataclass
class Observation:
    """What the replay saw in Postgres after one step settled."""

    index: int
    replay_t: str
    event_kind: str
    event_label: str
    move: MoveSnapshot | None = None
    new_signals: list[NewSignal] = field(default_factory=list)
    new_judgements: list[str] = field(default_factory=list)
    llm_calls: int = 0


def observation_from_dict(raw: Mapping[str, Any]) -> Observation:
    move = raw.get("move")
    return Observation(
        index=int(raw["index"]),
        replay_t=str(raw["replay_t"]),
        event_kind=str(raw["event_kind"]),
        event_label=str(raw["event_label"]),
        move=MoveSnapshot(**move) if move else None,
        new_signals=[NewSignal(**s) for s in raw.get("new_signals", [])],
        new_judgements=[str(j) for j in raw.get("new_judgements", [])],
        llm_calls=int(raw.get("llm_calls", 0)),
    )


# ---------------------------------------------------------------- records


def _epoch_millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def news_idempotency_key(symbol: str, source_doc_id: str, title: str) -> str:
    title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return f"{NEWS_SOURCE}:{symbol}:{source_doc_id}:{title_hash}"


def news_raw_id(idempotency_key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"invest-view:news-raw:{idempotency_key}"))


def news_record(
    symbol: str, item: Mapping[str, str], now: datetime | None = None
) -> dict[str, object]:
    """news-raw value for one prepared news row, keyed like the KIS poller."""
    received = now or datetime.now(timezone.utc)
    published = datetime.fromisoformat(item["published_at"])
    source_doc_id = str(item["srno"])
    title = str(item["title"])
    key = news_idempotency_key(symbol, source_doc_id, title)
    return {
        "raw_id": news_raw_id(key),
        "idempotency_key": key,
        "symbol": symbol,
        "source_doc_id": source_doc_id,
        "title": title,
        "provider_code": None,
        "category_code": None,
        "published_at": _epoch_millis(published),
        "observed_at": _epoch_millis(received),
        "received_at": _epoch_millis(received),
        "source_payload": {
            "cntt_usiq_srno": source_doc_id,
            "hts_pbnt_titl_cntt": title,
            "dorg": str(item.get("provider", "")),
        },
    }


def count_llm_attempts(raw: object) -> int:
    """Count real LLM calls in ``llm_attempts`` JSONB (asyncpg returns it as text).

    Cache hits (``cached`` true) cost nothing, so they do not count toward the cap.
    """
    if raw is None:
        return 0
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, list):
        return 0
    return sum(1 for attempt in value if not (isinstance(attempt, dict) and attempt.get("cached") is True))


# ---------------------------------------------------------------- idle wait

Probe = Callable[[], Awaitable[tuple[int, tuple[object, ...]]]]


async def wait_idle(
    probe: Probe,
    *,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], float],
    stable_checks: int = 3,
    interval: float = 1.0,
    timeout: float = 300.0,
) -> tuple[object, ...]:
    """Wait until the outbox is empty and the fingerprint is unchanged ``stable_checks`` times."""
    deadline = clock() + timeout
    previous: tuple[object, ...] | None = None
    stable = 0
    while True:
        pending, fingerprint = await probe()
        if pending == 0 and fingerprint == previous:
            stable += 1
        else:
            stable = 0
        previous = fingerprint
        if stable >= stable_checks:
            return fingerprint
        if clock() >= deadline:
            raise TimeoutError(
                f"replay step not idle after {timeout:.0f}s (outbox pending {pending}, fingerprint {fingerprint})"
            )
        await sleep(interval)


# ---------------------------------------------------------------- observation mapping


def _label(state: str) -> str:
    return STATE_LABELS.get(state, state)


def observations_to_states(
    observations: Sequence[Observation], move_id: str
) -> list[StateRow]:
    rows: list[StateRow] = []
    last: tuple[int, str] | None = None
    for obs in observations:
        move = obs.move
        if move is None or (move.version, move.price_state) == last:
            continue
        last = (move.version, move.price_state)
        rows.append(
            StateRow(
                t=datetime.fromisoformat(obs.replay_t),
                state=move.price_state,
                move_id=move_id,
                move_version=move.version,
                return_pct=move.return_pct,
            )
        )
    return rows


def decisions_to_log(
    decisions: Sequence[Mapping[str, Any]], clock: Callable[[datetime], datetime | None]
) -> list[LogEntry]:
    """KEEP rows of gold.signal_decision as log lines on the replay clock."""
    entries: list[LogEntry] = []
    for row in decisions:
        if row["decision"] != "KEEP":
            continue
        at = clock(row["created_at"])
        if at is not None:
            entries.append(LogEntry(at, "signal", f"기존 문구 유지: {row['reason']}"))
    return entries


def observations_to_log(observations: Sequence[Observation]) -> list[LogEntry]:
    log: list[LogEntry] = []
    last_move: MoveSnapshot | None = None
    for obs in observations:
        t = datetime.fromisoformat(obs.replay_t)
        if obs.event_kind == "news":
            log.append(LogEntry(t, "document", f"기사를 수신했습니다: {obs.event_label}"))
        move = obs.move
        if move is not None and (
            last_move is None
            or (move.version, move.price_state) != (last_move.version, last_move.price_state)
        ):
            label = _label(move.price_state)
            prefix = "DAILY move가 생성되었고, " if last_move is None else ""
            log.append(
                LogEntry(
                    t,
                    "state",
                    f"{prefix}가격 상태가 {label}{particle(label)} 바뀌었습니다. "
                    f"수익률은 {move.return_pct:+.2f}%입니다.",
                )
            )
        if move is not None:
            last_move = move
        if obs.new_judgements:
            log.append(
                LogEntry(t, "judgement", f"기사 {len(obs.new_judgements)}건의 판정을 마쳤습니다.")
            )
        if obs.llm_calls:
            log.append(LogEntry(t, "llm", f"LLM을 {obs.llm_calls}회 호출했습니다."))
        for sig in obs.new_signals:
            if sig.decided_by == "llm":
                text = f"시그널 v{sig.version}을 {sig.status} 상태로 기록했습니다."
                log.append(LogEntry(t, "signal", text))
            else:
                text = f"LLM 판단 없이 시그널 v{sig.version}을 {sig.status} 상태로 기록했습니다."
                log.append(LogEntry(t, "fallback", text))
    return log


def signal_steps(observations: Sequence[Observation]) -> dict[int, Observation]:
    """Signal version to the observation in which it first appeared."""
    seen: dict[int, Observation] = {}
    for obs in observations:
        for sig in obs.new_signals:
            seen.setdefault(sig.version, obs)
    return seen


def triggers(move_versions: Sequence[tuple[int, int]]) -> dict[int, str]:
    """(signal version, move version) pairs, ordered by version, to request reasons."""
    result: dict[int, str] = {}
    previous: int | None = None
    for version, move_version in sorted(move_versions):
        result[version] = "PRICE_STATE_CHANGE" if move_version != previous else "NEW_DOCUMENT"
        previous = move_version
    return result


def bars_from_inputs(raw: Sequence[Mapping[str, Any]]) -> list[Bar]:
    return [
        Bar(
            t=datetime.fromisoformat(b["t"]),
            open=int(b["o"]),
            high=int(b["h"]),
            low=int(b["l"]),
            close=int(b["c"]),
            volume=int(b["v"]),
        )
        for b in raw
    ]


def _float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return float(str(value))


def decomposition_from_move(row: Mapping[str, object]) -> dict[str, object] | None:
    keys = ("contrib_global", "contrib_market", "contrib_sector", "contrib_idio")
    if all(row.get(k) is None for k in keys):
        return None
    return {
        "global": _float(row.get("contrib_global")),
        "market": _float(row.get("contrib_market")),
        "sector": _float(row.get("contrib_sector")),
        "idio": _float(row.get("contrib_idio")),
        "dominant_layer": row.get("dominant_layer"),
    }


def build_signal_rows(
    signals: Sequence[Mapping[str, Any]],
    causes: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    observations: Sequence[Observation],
    fallback_at: Callable[[datetime], datetime | None],
) -> list[SignalRow]:
    first_seen = signal_steps(observations)
    reasons = triggers([(int(s["version"]), int(s["move_version"])) for s in signals])
    rows: list[SignalRow] = []
    for sig in sorted(signals, key=lambda s: int(s["version"])):
        version = int(sig["version"])
        obs = first_seen.get(version)
        at = datetime.fromisoformat(obs.replay_t) if obs else fallback_at(sig["decided_at"])
        if at is None:
            continue
        cause_rows = [
            CauseRow(
                rank=int(c["rank"]),
                label=str(c["label"]),
                explanation=str(c["explanation"] or ""),
                scope=str(c["scope"] or ""),
                source_document_ids=[
                    str(src["source_id"])
                    for src in sources
                    if int(src["version"]) == version and int(src["rank"]) == int(c["rank"])
                ],
            )
            for c in sorted(causes, key=lambda c: int(c["rank"]))
            if int(c["version"]) == version
        ]
        headline = next(
            (c.label for c in cause_rows if c.rank == sig["headline_cause_rank"]), ""
        )
        rows.append(
            SignalRow(
                at=at,
                move_version=int(sig["move_version"]),
                version=version,
                status=str(sig["status"]),
                headline_label=headline,
                decided_by=sig["decided_by"],
                model=sig["model_version"],
                prompt_version=sig["prompt_version"],
                trigger=reasons[version],
                causes=cause_rows,
                decomposition=obs.move.decomposition if obs and obs.move else None,
            )
        )
    return rows


def build_document_rows(
    documents: Sequence[Mapping[str, Any]], publishers: Mapping[str, str]
) -> list[DocumentRow]:
    return [
        DocumentRow(
            id=str(d["document_id"]),
            title=str(d["title"]),
            publisher=str(d["publisher"] or publishers.get(str(d["source_doc_id"]), "")),
            published_at=d["published_at"],
            judged=d["j_version"] is not None,
            direction=d["direction"],
            scope=d["scope"],
            fail_reason=d["fail_reason"],
        )
        for d in documents
    ]

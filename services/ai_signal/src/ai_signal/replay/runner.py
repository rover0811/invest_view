"""Kafka and Postgres I/O behind ``driver.run_replay`` and ``driver.export_replay``."""

from __future__ import annotations

import asyncio
import json
import time as time_mod
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ai_signal.replay import cluster
from ai_signal.replay.driver import (
    ReplayState,
    Step,
    daily_move_id,
    inputs_path,
    load_state,
    save_state,
    to_replay_clock,
)
from ai_signal.replay.export import build_export, check_no_lookahead
from ai_signal.replay.ticks import bar_to_tick
from ai_signal.replay.timeline import ReplayEvent, merge_events

_ENTITY = "SELECT entity_id FROM reference.entity_symbol WHERE ticker = $1"
_PROBE = """
SELECT
  (SELECT count(*) FROM outbox.events),
  (SELECT version FROM gold.move WHERE move_id = $1),
  (SELECT price_state FROM gold.move WHERE move_id = $1),
  (SELECT count(*) FROM gold.signal WHERE move_id = $1),
  (SELECT count(*) FROM silver.document_entity WHERE entity_id = $2),
  (SELECT count(*) FROM silver.document_judgement j
     JOIN silver.document_entity e USING (document_id) WHERE e.entity_id = $2)
"""
_MOVE = """
SELECT version, price_state, window_end, return_pct, signal_status,
       contrib_global, contrib_market, contrib_sector, contrib_idio, dominant_layer
FROM gold.move WHERE move_id = $1
"""
_EXPORT_DECISIONS = "SELECT decision, reason, created_at FROM gold.signal_decision WHERE move_id = $1 ORDER BY id"
_SIGNALS = "SELECT version, status, decided_by, llm_attempts FROM gold.signal WHERE move_id = $1"
_JUDGEMENTS = """
SELECT j.document_id, j.version, j.llm_attempts FROM silver.document_judgement j
JOIN silver.document_entity e USING (document_id) WHERE e.entity_id = $1
"""
_EXPORT_SIGNALS = """
SELECT version, move_version, status, headline_cause_rank, decided_by, model_version,
       prompt_version, decided_at
FROM gold.signal WHERE move_id = $1 ORDER BY version
"""
_EXPORT_CAUSES = "SELECT version, rank, label, explanation, scope FROM gold.signal_cause WHERE move_id = $1"
_EXPORT_SOURCES = "SELECT version, rank, source_type, source_id FROM gold.signal_cause_source WHERE move_id = $1"
_EXPORT_DOCUMENTS = """
SELECT DISTINCT ON (d.document_id)
       d.document_id, d.source_doc_id, d.title, d.publisher, d.published_at,
       j.version AS j_version, j.direction, j.scope, j.fail_reason
FROM silver.document d
JOIN silver.document_entity e ON e.document_id = d.document_id AND e.entity_id = $1
LEFT JOIN LATERAL (
  SELECT version, direction, scope, fail_reason FROM silver.document_judgement
  WHERE document_id = d.document_id ORDER BY version DESC LIMIT 1
) j ON true
WHERE d.published_at BETWEEN $2 AND $3
ORDER BY d.document_id, d.version DESC
"""


def _load_inputs(symbol: str, day: date) -> dict[str, Any]:
    path = inputs_path(symbol, day)
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run `prepare` first")
    return json.loads(path.read_text(encoding="utf-8"))


def build_events(inputs: dict[str, Any]) -> list[ReplayEvent]:
    symbol = str(inputs["symbol"])
    prev_close = int(inputs["prev_close"])
    ticks = [bar_to_tick(b, prev_close, symbol) for b in cluster.bars_from_inputs(inputs["bars"])]
    news = [cluster.news_record(symbol, n) for n in inputs["news"]]
    return merge_events(ticks, news)


def _event_label(event: ReplayEvent) -> str:
    if event.kind == "news":
        return str(event.payload["title"])
    return f"{event.payload['trade_time']} {event.payload['price']}"


def _judgement_key(document_id: object, version: object) -> str:
    return f"{document_id}:{version}"


async def _entity_id(conn: Any, symbol: str) -> Any:
    entity = await conn.fetchval(_ENTITY, symbol)
    if entity is None:
        raise RuntimeError(f"no reference.entity_symbol row for {symbol}")
    return entity


async def _observe(
    conn: Any, move_id: str, entity_id: Any, state: ReplayState, event: ReplayEvent, index: int
) -> cluster.Observation:
    seen_signals = {s.version for o in state.observations for s in o.new_signals}
    seen_signals.update(state.baseline_signals)
    seen_judgements = {j for o in state.observations for j in o.new_judgements}
    seen_judgements.update(state.baseline_judgements)

    move_row = await conn.fetchrow(_MOVE, move_id)
    move = None
    if move_row is not None:
        move = cluster.MoveSnapshot(
            version=int(move_row["version"]),
            price_state=str(move_row["price_state"]),
            window_end=move_row["window_end"].isoformat(),
            return_pct=float(move_row["return_pct"]),
            signal_status=move_row["signal_status"],
            decomposition=cluster.decomposition_from_move(dict(move_row)),
        )
    llm_calls = 0
    new_signals: list[cluster.NewSignal] = []
    for row in await conn.fetch(_SIGNALS, move_id):
        if int(row["version"]) in seen_signals:
            continue
        new_signals.append(cluster.NewSignal(int(row["version"]), str(row["status"]), row["decided_by"]))
        llm_calls += cluster.count_llm_attempts(row["llm_attempts"])
    new_judgements: list[str] = []
    for row in await conn.fetch(_JUDGEMENTS, entity_id):
        key = _judgement_key(row["document_id"], row["version"])
        if key in seen_judgements:
            continue
        new_judgements.append(key)
        llm_calls += cluster.count_llm_attempts(row["llm_attempts"])
    return cluster.Observation(
        index=index,
        replay_t=event.time.isoformat(timespec="seconds"),
        event_kind=event.kind,
        event_label=_event_label(event),
        move=move,
        new_signals=sorted(new_signals, key=lambda s: s.version),
        new_judgements=sorted(new_judgements),
        llm_calls=llm_calls,
    )


async def _baseline(conn: Any, move_id: str, entity_id: Any, state: ReplayState) -> None:
    state.baseline_signals = sorted(int(r["version"]) for r in await conn.fetch(_SIGNALS, move_id))
    state.baseline_judgements = sorted(
        _judgement_key(r["document_id"], r["version"]) for r in await conn.fetch(_JUDGEMENTS, entity_id)
    )


class _Publisher:
    def __init__(self, bootstrap: str, schema_registry: str) -> None:
        from confluent_kafka import Producer

        from ai_signal.avro import AvroValueSerializer, schema_registry_client

        self._producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})
        self._serializer = AvroValueSerializer(schema_registry_client(schema_registry))

    def publish(self, event: ReplayEvent, symbol: str) -> None:
        topic = cluster.NEWS_TOPIC if event.kind == "news" else cluster.TICKS_TOPIC
        errors: list[str] = []

        def on_delivery(err: object, _msg: object) -> None:
            if err is not None:
                errors.append(str(err))

        self._producer.produce(
            topic,
            key=symbol.encode("utf-8"),
            value=self._serializer.serialize(topic, event.payload),
            on_delivery=on_delivery,
        )
        remaining = self._producer.flush(30)
        if remaining or errors:
            raise RuntimeError(f"produce to {topic} failed: remaining={remaining} errors={errors}")


async def _run(
    symbol: str, day: date, *, bootstrap: str, schema_registry: str, pg_dsn: str, max_llm_calls: int
) -> ReplayState:
    import asyncpg

    events = build_events(_load_inputs(symbol, day))
    state = load_state(symbol, day)
    move_id = daily_move_id(symbol, day)
    publisher = _Publisher(bootstrap, schema_registry)
    conn = await asyncpg.connect(pg_dsn)
    try:
        entity_id = await _entity_id(conn, symbol)
        if state.next_event == 0 and not state.observations:
            await _baseline(conn, move_id, entity_id, state)
            save_state(state)

        async def probe() -> tuple[int, tuple[object, ...]]:
            row = await conn.fetchrow(_PROBE, move_id, entity_id)
            return int(row[0]), tuple(row[1:])

        while state.next_event < len(events):
            if state.llm_calls >= max_llm_calls:
                print(f"LLM call cap {max_llm_calls} reached ({state.llm_calls}); state saved, rerun to resume")
                break
            index = state.next_event
            event = events[index]
            wall_start = datetime.now(timezone.utc)
            publisher.publish(event, symbol)
            await cluster.wait_idle(probe, sleep=asyncio.sleep, clock=time_mod.monotonic)
            wall_end = datetime.now(timezone.utc)
            observation = await _observe(conn, move_id, entity_id, state, event, index)
            step = Step(
                replay_t=observation.replay_t,
                wall_start=wall_start.isoformat(),
                wall_end=wall_end.isoformat(),
            )
            state.record(step, observation.llm_calls, observation)
            save_state(state)
            print(
                f"step {index + 1}/{len(events)} {event.kind} {observation.replay_t} "
                f"llm {state.llm_calls} ({(wall_end - wall_start).total_seconds():.1f}s)"
            )
    finally:
        await conn.close()
    return state


def run_replay(
    symbol: str, day: date, *, bootstrap: str, schema_registry: str, pg_dsn: str, max_llm_calls: int = 400
) -> ReplayState:
    return asyncio.run(
        _run(
            symbol,
            day,
            bootstrap=bootstrap,
            schema_registry=schema_registry,
            pg_dsn=pg_dsn,
            max_llm_calls=max_llm_calls,
        )
    )


async def _export(symbol: str, day: date, pg_dsn: str) -> dict[str, object]:
    import asyncpg

    inputs = _load_inputs(symbol, day)
    state = load_state(symbol, day)
    move_id = daily_move_id(symbol, day)
    start, end = (datetime.fromisoformat(t) for t in inputs["candidate_range"])
    conn = await asyncpg.connect(pg_dsn)
    try:
        entity_id = await _entity_id(conn, symbol)
        documents = [dict(r) for r in await conn.fetch(_EXPORT_DOCUMENTS, entity_id, start, end)]
        signals = [dict(r) for r in await conn.fetch(_EXPORT_SIGNALS, move_id)]
        causes = [dict(r) for r in await conn.fetch(_EXPORT_CAUSES, move_id)]
        sources = [dict(r) for r in await conn.fetch(_EXPORT_SOURCES, move_id)]
        decisions = [dict(r) for r in await conn.fetch(_EXPORT_DECISIONS, move_id)]
    finally:
        await conn.close()
    documents.sort(key=lambda d: d["published_at"])
    publishers = {str(n["srno"]): str(n["provider"]) for n in inputs["news"]}
    export = build_export(
        symbol=symbol,
        name=str(inputs["name"]),
        day=day,
        prev_close=int(inputs["prev_close"]),
        bars=cluster.bars_from_inputs(inputs["bars"]),
        states=cluster.observations_to_states(state.observations, move_id),
        documents=cluster.build_document_rows(documents, publishers),
        signals=cluster.build_signal_rows(
            signals, causes, sources, state.observations, lambda at: to_replay_clock(at, state.steps)
        ),
        log=[
            *cluster.observations_to_log(state.observations),
            *cluster.decisions_to_log(decisions, lambda at: to_replay_clock(at, state.steps)),
        ],
    )
    check_no_lookahead(export)
    return export


def export_replay(symbol: str, day: date, *, pg_dsn: str, out_path: Path) -> Path:
    export = asyncio.run(_export(symbol, day, pg_dsn))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(export, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return out_path

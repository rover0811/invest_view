from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

import pytest

from ai_signal.replay import cluster
from ai_signal.replay.cluster import MoveSnapshot, NewSignal, Observation
from ai_signal.replay.driver import ReplayState, Step, load_state, save_state
from ai_signal.replay.export import build_export, check_no_lookahead

NEWS = {
    "srno": "2026101409100001",
    "title": "SK하이닉스, HBM 공급 계약",
    "provider": "머니투데이",
    "published_at": "2026-10-14T09:10:00+09:00",
}


def test_news_record_matches_poller_keys() -> None:
    record = cluster.news_record(
        "000660", NEWS, datetime.fromisoformat("2026-10-21T01:00:00+00:00")
    )
    title_hash = hashlib.sha256(NEWS["title"].encode("utf-8")).hexdigest()
    key = f"kis_news:000660:{NEWS['srno']}:{title_hash}"
    assert record["idempotency_key"] == key
    assert record["raw_id"] == str(uuid5(NAMESPACE_URL, f"invest-view:news-raw:{key}"))
    assert record["published_at"] == int(
        datetime.fromisoformat(NEWS["published_at"]).timestamp() * 1000
    )
    assert record["provider_code"] is None
    assert record["source_payload"]["dorg"] == "머니투데이"


def test_count_llm_attempts_handles_text_list_and_null() -> None:
    assert cluster.count_llm_attempts('[{"model": "a"}, {"model": "b"}]') == 2
    assert cluster.count_llm_attempts([{"model": "a"}]) == 1
    assert cluster.count_llm_attempts(None) == 0


def test_count_llm_attempts_excludes_cache_hits() -> None:
    raw = '[{"model": "a", "cached": true}, {"model": "b", "cached": false}, {"model": "c"}]'
    assert cluster.count_llm_attempts(raw) == 2
    assert cluster.count_llm_attempts([{"model": "a", "cached": True}]) == 0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _probe(readings: list[tuple[int, tuple[object, ...]]]) -> cluster.Probe:
    it = iter(readings)

    async def probe() -> tuple[int, tuple[object, ...]]:
        return next(it)

    return probe


def test_wait_idle_needs_empty_outbox_and_three_stable_checks() -> None:
    clock = _Clock()
    readings: list[tuple[int, tuple[object, ...]]] = [
        (2, (1,)),
        (0, (1,)),
        (0, (2,)),
        (0, (2,)),
        (0, (2,)),
        (0, (2,)),
    ]
    result = asyncio.run(
        cluster.wait_idle(_probe(readings), sleep=clock.sleep, clock=clock)
    )
    assert result == (2,)
    assert clock.now == 5.0


def test_wait_idle_times_out() -> None:
    clock = _Clock()
    readings = [(1, (i,)) for i in range(20)]
    with pytest.raises(TimeoutError, match="outbox pending 1"):
        asyncio.run(
            cluster.wait_idle(
                _probe(readings), sleep=clock.sleep, clock=clock, timeout=5
            )
        )


def _obs(index: int, t: str, **kw: object) -> Observation:
    defaults: dict[str, object] = {"event_kind": "tick", "event_label": "0900 1"}
    defaults.update(kw)
    return Observation(index=index, replay_t=f"2026-10-14T{t}:00+09:00", **defaults)


MOVE1 = MoveSnapshot(
    1, "POSITIVE", "2026-10-14T09:24:00+09:00", 1.08, "PENDING", {"idio": 0.8}
)
MOVE1_LATER = MoveSnapshot(1, "POSITIVE", "2026-10-14T09:25:00+09:00", 1.2)
MOVE2 = MoveSnapshot(2, "NEUTRAL", "2026-10-14T10:00:00+09:00", 0.4)


def _observations() -> list[Observation]:
    return [
        _obs(
            0,
            "09:10",
            event_kind="news",
            event_label=NEWS["title"],
            new_judgements=["d1:1"],
            llm_calls=1,
        ),
        _obs(
            1,
            "09:24",
            move=MOVE1,
            new_signals=[NewSignal(1, "SHOWN", "llm")],
            llm_calls=2,
        ),
        _obs(2, "09:25", move=MOVE1_LATER),
        _obs(3, "10:00", move=MOVE2, new_signals=[NewSignal(2, "HIDDEN", None)]),
    ]


def test_observations_to_states_emit_only_changes() -> None:
    states = cluster.observations_to_states(_observations(), "m")
    assert [
        (s.t.strftime("%H:%M"), s.state, s.move_version, s.return_pct) for s in states
    ] == [
        ("09:24", "POSITIVE", 1, 1.08),
        ("10:00", "NEUTRAL", 2, 0.4),
    ]


def test_observations_to_log_is_korean_without_arrows() -> None:
    log = cluster.observations_to_log(_observations())
    assert [e.kind for e in log] == [
        "document",
        "judgement",
        "llm",
        "state",
        "llm",
        "signal",
        "state",
        "fallback",
    ]
    texts = " ".join(e.text for e in log)
    assert (
        "DAILY move가 생성되었고, 가격 상태가 긍정으로 바뀌었습니다. 수익률은 +1.08%입니다."
        in texts
    )
    assert "가격 상태가 중립으로 바뀌었습니다." in texts
    for mark in ("\u2192", "\u2014", "\u2013", "\u00b7"):
        assert mark not in texts


def test_build_signal_rows_uses_first_seen_step_and_trigger() -> None:
    signals = [
        {
            "version": v,
            "move_version": mv,
            "status": "SHOWN",
            "headline_cause_rank": 1,
            "decided_by": "llm",
            "model_version": "m",
            "prompt_version": "p",
            "decided_at": datetime.fromisoformat("2026-10-21T01:00:00+00:00"),
        }
        for v, mv in ((1, 1), (2, 1))
    ]
    causes = [
        {
            "version": 1,
            "rank": 1,
            "label": "HBM 계약",
            "explanation": "e",
            "scope": "SYMBOL",
        }
    ]
    sources = [{"version": 1, "rank": 1, "source_type": "document", "source_id": "d1"}]
    obs = _observations()[:2]
    rows = cluster.build_signal_rows(signals, causes, sources, obs, lambda _at: None)
    assert len(rows) == 1
    row = rows[0]
    assert row.at.isoformat() == "2026-10-14T09:24:00+09:00"
    assert (row.trigger, row.headline_label, row.decomposition) == (
        "PRICE_STATE_CHANGE",
        "HBM 계약",
        {"idio": 0.8},
    )
    assert list(row.causes[0].source_document_ids) == ["d1"]
    fallback = datetime.fromisoformat("2026-10-14T09:30:00+09:00")
    rows = cluster.build_signal_rows(
        signals, causes, sources, obs, lambda _at: fallback
    )
    assert rows[1].trigger == "NEW_DOCUMENT" and rows[1].at == fallback

    documents = cluster.build_document_rows(
        [
            {
                "document_id": "d1",
                "source_doc_id": NEWS["srno"],
                "title": "t",
                "publisher": None,
                "published_at": datetime.fromisoformat(NEWS["published_at"]),
                "j_version": 1,
                "direction": 1,
                "scope": "SYMBOL",
                "fail_reason": None,
            }
        ],
        {NEWS["srno"]: "머니투데이"},
    )
    assert documents[0].publisher == "머니투데이" and documents[0].judged
    export = build_export(
        symbol="000660",
        name="n",
        day=datetime(2026, 10, 14).date(),
        prev_close=1,
        bars=[],
        states=[],
        documents=documents,
        signals=rows,
        log=[],
    )
    check_no_lookahead(export)


def test_state_round_trip_keeps_observations(tmp_path) -> None:
    state = ReplayState(symbol="000660", day="2026-10-14", baseline_judgements=["x:1"])
    step = Step(
        "2026-10-14T09:24:00+09:00",
        "2026-10-21T01:00:00+00:00",
        "2026-10-21T01:00:04+00:00",
    )
    state.record(step, 2, _observations()[1])
    save_state(state, tmp_path)
    loaded = load_state("000660", datetime(2026, 10, 14).date(), tmp_path)
    assert loaded == state


def test_keep_decisions_become_signal_log_entries_on_the_replay_clock() -> None:
    from datetime import UTC, datetime, timedelta

    from ai_signal.replay import cluster as replay_cluster

    created = datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
    rows = [
        {"decision": "KEEP", "reason": "같은 사건이에요.", "created_at": created},
        {"decision": "UPDATE", "reason": "새 사건이에요.", "created_at": created},
        {"decision": "KEEP", "reason": "범위 밖이에요.", "created_at": created + timedelta(days=1)},
    ]
    entries = replay_cluster.decisions_to_log(
        rows, lambda at: at + timedelta(hours=1) if at == created else None
    )
    assert [(e.t, e.kind, e.text) for e in entries] == [
        (created + timedelta(hours=1), "signal", "기존 문구 유지: 같은 사건이에요.")
    ]

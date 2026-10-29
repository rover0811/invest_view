from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path

from ai_signal.replay import driver
from ai_signal.replay.bars import KST
from ai_signal.replay.driver import ReplayState, Step


def test_daily_move_id_matches_uuid5_dns_layout() -> None:
    expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "000660|2026-10-20|DAILY"))
    assert driver.daily_move_id("000660", date(2026, 10, 20)) == expected


def _step(replay: str, start: str, end: str) -> Step:
    return Step(replay_t=replay, wall_start=start, wall_end=end)


def test_state_round_trip_resumes(tmp_path: Path) -> None:
    day = date(2026, 10, 20)
    fresh = driver.load_state("000660", day, tmp_path)
    assert (fresh.next_event, fresh.llm_calls, fresh.steps) == (0, 0, [])
    fresh.record(
        _step(
            "2026-10-20T09:00:00+09:00",
            "2026-10-21T10:00:00+00:00",
            "2026-10-21T10:00:05+00:00",
        ),
        2,
    )
    driver.save_state(fresh, tmp_path)
    loaded = driver.load_state("000660", day, tmp_path)
    assert loaded == fresh
    assert loaded.next_event == 1
    assert loaded.llm_calls == 2


def test_capped_at_llm_limit() -> None:
    state = ReplayState(
        symbol="000660", day="2026-10-20", llm_calls=driver.LLM_CALL_CAP - 1
    )
    assert not state.capped
    state.llm_calls += 1
    assert state.capped


def test_to_replay_clock_picks_latest_started_step() -> None:
    steps = [
        _step(
            "2026-10-20T09:00:00+09:00",
            "2026-10-21T10:00:00+00:00",
            "2026-10-21T10:00:05+00:00",
        ),
        _step(
            "2026-10-20T09:01:00+09:00",
            "2026-10-21T10:00:10+00:00",
            "2026-10-21T10:00:20+00:00",
        ),
    ]
    inside_second = datetime.fromisoformat("2026-10-21T10:00:15+00:00")
    gap_after_first = datetime.fromisoformat("2026-10-21T10:00:07+00:00")
    before_any = datetime.fromisoformat("2026-10-21T09:59:59+00:00")
    assert driver.to_replay_clock(inside_second, steps) == datetime(
        2026, 10, 20, 9, 1, tzinfo=KST
    )
    assert driver.to_replay_clock(gap_after_first, steps) == datetime(
        2026, 10, 20, 9, 0, tzinfo=KST
    )
    assert driver.to_replay_clock(before_any, steps) is None


def test_candidate_range_spans_prev_close_to_evening() -> None:
    start, end = driver.candidate_range(date(2026, 10, 13), date(2026, 10, 14))
    assert start == datetime(2026, 10, 13, 15, 30, tzinfo=KST)
    assert end == datetime(2026, 10, 14, 20, 0, tzinfo=KST)


def test_news_in_range_filters_and_sorts() -> None:
    start, end = driver.candidate_range(date(2026, 10, 13), date(2026, 10, 14))
    rows = [
        {"time": "153000", "title": "edge", "provider": "a", "srno": "1"},
        {"time": "152959", "title": "early", "provider": "a", "srno": "2"},
        {"time": "90000", "title": "morning", "provider": "b", "srno": "3"},
    ]
    prev = driver.news_in_range(rows, date(2026, 10, 13), start, end)
    assert [n["title"] for n in prev] == ["edge"]
    today = driver.news_in_range(
        [*rows, {"time": "200001", "title": "late", "provider": "c", "srno": "4"}],
        date(2026, 10, 14),
        start,
        end,
    )
    assert [n["title"] for n in today] == ["morning", "early", "edge"]
    assert today[0]["published_at"] == "2026-10-14T09:00:00+09:00"


def test_cleanup_deletes_children_before_parents() -> None:
    order = [table for table, _ in driver.CLEANUP_STATEMENTS]
    assert order.index("serving.symbol_signal") < order.index("gold.move")
    assert order.index("serving.symbol_signal_cause_source") < order.index(
        "serving.symbol_signal_cause"
    )
    assert order.index("serving.symbol_signal_history_cause") < order.index(
        "serving.symbol_signal_history"
    )
    for child in (
        "gold.signal_cause_source",
        "gold.signal_cause",
        "gold.signal",
        "gold.signal_retry",
    ):
        assert order.index(child) < order.index("gold.move")
    assert (
        order.index("gold.signal_cause_source")
        < order.index("gold.signal_cause")
        < order.index("gold.signal")
    )


def test_cleanup_args_bind_stale_move_id_only_for_gold() -> None:
    stale = date(2026, 10, 20)
    assert driver.cleanup_args("serving.symbol_signal", "000660", stale) == ("000660",)
    assert driver.cleanup_args("gold.move", "000660", stale) == (
        "000660",
        stale,
        driver.daily_move_id("000660", stale),
    )


def test_cleanup_keeps_the_llm_cache() -> None:
    for table, sql in driver.CLEANUP_STATEMENTS:
        assert table != "gold.llm_cache"
        assert "llm_cache" not in sql

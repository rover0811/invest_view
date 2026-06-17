from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from tick_persistence.aggregation.ohlc import BarState, FiveMinuteAggregator, KST

_DEFAULT_VWAP = object()


def _tick(
    trade_time: str,
    price: int,
    volume: int = 10,
    *,
    symbol: str = "005930",
    business_date: str = "20260601",
    vwap: Decimal | None | object = _DEFAULT_VWAP,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "business_date": business_date,
        "trade_time": trade_time,
        "price": price,
        "trade_volume": volume,
        "vwap": Decimal(price) if vwap is _DEFAULT_VWAP else vwap,
        "cumulative_volume": 999999,
    }


def _event_ts(tick: dict[str, Any]) -> datetime:
    return datetime.strptime(f"{tick['business_date']}{tick['trade_time']}", "%Y%m%d%H%M%S").replace(tzinfo=KST)


def _add_tick(
    agg: FiveMinuteAggregator,
    tick: dict[str, Any],
    *,
    partition: int = 0,
    offset: int,
) -> tuple[datetime, BarState]:
    return agg.add_tick(tick, event_ts=_event_ts(tick), partition=partition, offset=offset)


def _visible(bar: BarState) -> tuple[Any, ...]:
    return (bar.open, bar.high, bar.low, bar.close, bar.volume, bar.vwap_last, bar.tick_count, bar.is_final)


def test_bucket_start_uses_kst_and_floors_to_5_minutes():
    agg = FiveMinuteAggregator()
    assert agg.bucket_start("20260601", "090321") == datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    assert agg.bucket_start("20260601", "090700") == datetime(2026, 6, 1, 9, 5, tzinfo=KST)
    assert agg.bucket_start("20260601", "090459") == datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    assert agg.bucket_start("20260601", "090500") == datetime(2026, 6, 1, 9, 5, tzinfo=KST)


def test_multi_tick_bucket_exact_ohlc_volume_vwap_and_count():
    agg = FiveMinuteAggregator()
    ticks = [
        _tick("090301", 70000, 5, vwap=Decimal("70000.00000000")),
        _tick("090330", 70500, 7, vwap=Decimal("70250.00000000")),
        _tick("090405", 69800, 11, vwap=Decimal("70100.00000000")),
        _tick("090459", 70100, 13, vwap=Decimal("70050.00000000")),
    ]

    bucket = None
    bar = None
    for offset, tick in enumerate(ticks, start=1):
        bucket, bar = _add_tick(agg, tick, offset=offset)

    assert bucket == datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    assert bar is not None
    assert _visible(bar) == (70000, 70500, 69800, 70100, 36, Decimal("70050.00000000"), 4, False)
    assert bar.low <= bar.open <= bar.high
    assert bar.low <= bar.close <= bar.high


def test_reordered_same_inserted_tick_set_produces_same_bar_by_event_partition_offset_key():
    tick_entries = [
        (_tick("090330", 70500, 7), 0, 20),
        (_tick("090301", 70000, 5), 0, 10),
        (_tick("090459", 70100, 13), 1, 30),
        (_tick("090405", 69800, 11), 0, 40),
    ]
    forward = FiveMinuteAggregator()
    reordered = FiveMinuteAggregator()
    forward_bar: BarState | None = None
    reordered_bar: BarState | None = None

    for tick, partition, offset in tick_entries:
        _, forward_bar = _add_tick(forward, tick, partition=partition, offset=offset)
    for tick, partition, offset in reversed(tick_entries):
        _, reordered_bar = _add_tick(reordered, tick, partition=partition, offset=offset)

    assert forward_bar is not None
    assert reordered_bar is not None
    assert _visible(forward_bar) == _visible(reordered_bar)
    assert forward_bar.open_key == reordered_bar.open_key == (datetime(2026, 6, 1, 9, 3, 1, tzinfo=KST), 0, 10)
    assert forward_bar.close_key == reordered_bar.close_key == (datetime(2026, 6, 1, 9, 4, 59, tzinfo=KST), 1, 30)


def test_same_event_time_open_close_tie_breaks_by_partition_offset_and_keeps_close_vwap():
    agg = FiveMinuteAggregator()
    first = _tick("090100", 70000, 5, vwap=Decimal("70000.00000000"))
    lower_key_same_second = _tick("090100", 70500, 7, vwap=Decimal("70500.00000000"))
    later_same_second = _tick("090200", 69800, 11, vwap=Decimal("69800.00000000"))
    higher_key_same_second = _tick("090200", 70100, 13, vwap=Decimal("70100.00000000"))

    _add_tick(agg, higher_key_same_second, partition=3, offset=1)
    _add_tick(agg, first, partition=1, offset=2)
    _add_tick(agg, later_same_second, partition=0, offset=99)
    _, bar = _add_tick(agg, lower_key_same_second, partition=0, offset=1)

    assert _visible(bar) == (70500, 70500, 69800, 70100, 36, Decimal("70100.00000000"), 4, False)
    assert bar.open_key == (datetime(2026, 6, 1, 9, 1, tzinfo=KST), 0, 1)
    assert bar.close_key == (datetime(2026, 6, 1, 9, 2, tzinfo=KST), 3, 1)
    assert bar.low <= bar.open <= bar.high
    assert bar.low <= bar.close <= bar.high


def test_aggregator_counts_each_inserted_tick_once_and_keeps_no_observation_cache():
    agg = FiveMinuteAggregator()
    tick = _tick("090000", 70000, 10)

    _add_tick(agg, tick, partition=0, offset=1)
    _, bar = _add_tick(agg, tick, partition=0, offset=2)

    assert _visible(bar) == (70000, 70000, 70000, 70000, 20, Decimal("70000"), 2, False)
    assert not hasattr(bar, "_observations")


def test_boundary_split_marks_prior_bucket_final_and_single_tick_bar():
    agg = FiveMinuteAggregator()

    first_bucket, first = _add_tick(agg, _tick("090459", 70000, 3), offset=1)
    second_bucket, second = _add_tick(agg, _tick("090500", 70100, 4), offset=2)

    assert first_bucket == datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    assert second_bucket == datetime(2026, 6, 1, 9, 5, tzinfo=KST)
    assert _visible(first) == (70000, 70000, 70000, 70000, 3, Decimal("70000"), 1, True)
    assert _visible(second) == (70100, 70100, 70100, 70100, 4, Decimal("70100"), 1, False)
    assert agg.pop_finalized_bars() == [("005930", first_bucket, first)]


def test_single_tick_bar_maintains_ohlc_invariants():
    agg = FiveMinuteAggregator()
    _, bar = _add_tick(agg, _tick("101500", 12345, 1, vwap=None), offset=1)

    assert _visible(bar) == (12345, 12345, 12345, 12345, 1, None, 1, False)
    assert bar.low <= bar.open <= bar.high
    assert bar.low <= bar.close <= bar.high


def test_hydrate_existing_bar_then_add_tick_without_partial_overwrite():
    agg = FiveMinuteAggregator()
    bucket = datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    existing = BarState.from_existing(
        open=70000,
        high=70500,
        low=70000,
        close=70400,
        volume=30,
        vwap=Decimal("70300.00000000"),
        tick_count=3,
        open_key=(datetime(2026, 6, 1, 9, 0, tzinfo=KST), 0, 1),
        close_key=(datetime(2026, 6, 1, 9, 2, tzinfo=KST), 0, 3),
        is_final=False,
    )

    agg.hydrate("005930", bucket, existing)
    result_bucket, bar = _add_tick(agg, _tick("090430", 69900, 9, vwap=Decimal("70200.00000000")), offset=4)

    assert result_bucket == bucket
    assert bar is existing
    assert _visible(bar) == (70000, 70500, 69900, 69900, 39, Decimal("70200.00000000"), 4, False)


def test_finalized_bars_closes_elapsed_current_bucket():
    agg = FiveMinuteAggregator()
    bucket, bar = _add_tick(agg, _tick("090000", 70000, 1), offset=1)

    finalized = agg.finalized_bars(datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc))

    assert finalized == [("005930", bucket, bar)]
    assert bar.is_final is True
    assert agg._bars == {}


def test_pop_finalized_bars_evicts_returned_bars_and_bounds_memory():
    agg = FiveMinuteAggregator()
    flushed: list[tuple[str, datetime, BarState]] = []
    trade_times = ["090000", "090500", "091000", "091500", "092000", "092500"]

    for index, trade_time in enumerate(trade_times):
        _add_tick(agg, _tick(trade_time, 70000 + index, volume=index + 1), offset=index)
        flushed.extend(agg.pop_finalized_bars())
        assert len(agg._bars) <= 1

    assert [bucket for _, bucket, _ in flushed] == [
        datetime(2026, 6, 1, 9, 0, tzinfo=KST),
        datetime(2026, 6, 1, 9, 5, tzinfo=KST),
        datetime(2026, 6, 1, 9, 10, tzinfo=KST),
        datetime(2026, 6, 1, 9, 15, tzinfo=KST),
        datetime(2026, 6, 1, 9, 20, tzinfo=KST),
    ]
    assert [_visible(bar) for _, _, bar in flushed] == [
        (70000, 70000, 70000, 70000, 1, Decimal("70000"), 1, True),
        (70001, 70001, 70001, 70001, 2, Decimal("70001"), 1, True),
        (70002, 70002, 70002, 70002, 3, Decimal("70002"), 1, True),
        (70003, 70003, 70003, 70003, 4, Decimal("70003"), 1, True),
        (70004, 70004, 70004, 70004, 5, Decimal("70004"), 1, True),
    ]
    assert agg.pop_finalized_bars() == []
    assert set(agg._bars) == {("005930", datetime(2026, 6, 1, 9, 25, tzinfo=KST))}


def test_many_symbols_and_past_buckets_evict_finalized_bars_immediately():
    agg = FiveMinuteAggregator()
    symbols = [f"{index:06}" for index in range(40)]
    flushed: list[tuple[str, datetime, BarState]] = []

    offset = 0
    for bucket_index in range(10):
        minute = bucket_index * 5
        for symbol in symbols:
            for second in range(3):
                offset += 1
                _add_tick(
                    agg,
                    _tick(f"09{minute:02}{second:02}", 70_000 + bucket_index + second, symbol=symbol),
                    offset=offset,
                )
            flushed.extend(agg.pop_finalized_bars())
            assert len(agg._bars) <= len(symbols)

    assert len(flushed) == len(symbols) * 9
    assert len(agg._bars) == len(symbols)

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
    for tick in ticks:
        bucket, bar = agg.add_tick(tick)

    assert bucket == datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    assert bar is not None
    assert _visible(bar) == (70000, 70500, 69800, 70100, 36, Decimal("70050.00000000"), 4, False)
    assert bar.low <= bar.open <= bar.high
    assert bar.low <= bar.close <= bar.high


def test_reordered_same_tick_set_produces_same_bar_and_duplicate_replay_is_ignored():
    ticks = [
        _tick("090330", 70500, 7),
        _tick("090301", 70000, 5),
        _tick("090459", 70100, 13),
        _tick("090405", 69800, 11),
    ]
    forward = FiveMinuteAggregator()
    reordered = FiveMinuteAggregator()
    forward_bar: BarState | None = None
    reordered_bar: BarState | None = None

    for tick in ticks:
        _, forward_bar = forward.add_tick(tick)
    for tick in reversed(ticks):
        _, reordered_bar = reordered.add_tick(tick)

    assert forward_bar is not None
    assert reordered_bar is not None
    assert _visible(forward_bar) == _visible(reordered_bar)

    before = _visible(forward_bar)
    for tick in reversed(ticks):
        _, forward_bar = forward.add_tick(tick)
    assert _visible(forward_bar) == before


def test_boundary_split_marks_prior_bucket_final_and_single_tick_bar():
    agg = FiveMinuteAggregator()

    first_bucket, first = agg.add_tick(_tick("090459", 70000, 3))
    second_bucket, second = agg.add_tick(_tick("090500", 70100, 4))

    assert first_bucket == datetime(2026, 6, 1, 9, 0, tzinfo=KST)
    assert second_bucket == datetime(2026, 6, 1, 9, 5, tzinfo=KST)
    assert _visible(first) == (70000, 70000, 70000, 70000, 3, Decimal("70000"), 1, True)
    assert _visible(second) == (70100, 70100, 70100, 70100, 4, Decimal("70100"), 1, False)
    assert agg.pop_finalized_bars() == [("005930", first_bucket, first)]


def test_single_tick_bar_maintains_ohlc_invariants():
    agg = FiveMinuteAggregator()
    _, bar = agg.add_tick(_tick("101500", 12345, 1, vwap=None))

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
        is_final=False,
    )

    agg.hydrate("005930", bucket, existing)
    result_bucket, bar = agg.add_tick(_tick("090430", 69900, 9, vwap=Decimal("70200.00000000")))

    assert result_bucket == bucket
    assert bar is existing
    assert _visible(bar) == (70000, 70500, 69900, 69900, 39, Decimal("70200.00000000"), 4, False)


def test_finalized_bars_closes_elapsed_current_bucket():
    agg = FiveMinuteAggregator()
    bucket, bar = agg.add_tick(_tick("090000", 70000, 1))

    finalized = agg.finalized_bars(datetime(2026, 6, 1, 0, 5, tzinfo=timezone.utc))

    assert finalized == [("005930", bucket, bar)]
    assert bar.is_final is True

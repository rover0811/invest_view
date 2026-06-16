"""
Property-based tests for event_id invariants (I5: determinism, injectivity).

Verifies kis_ingestion.event_id.compute_event_id:
   - UUIDv5, namespace 6ba7b810-9dad-11d1-80b4-00c04fd430c8
   - Fields (in order): market, symbol, business_date, cumulative_volume,
     trade_time, price, trade_type
   - Pipe-delimited, all values normalized to str

Same input must always yield the same id (determinism), and a change to any
identity field must change the id (injectivity).
"""

import copy

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from kis_ingestion.event_id import compute_event_id

from kis_ingestion.tick_parser import ParsedTick


# ---------------------------------------------------------------------------
# Hypothesis strategy: minimal ParsedTick-compatible dict
# ---------------------------------------------------------------------------

# Markets supported by KIS
_MARKETS = st.sampled_from(["KRX", "NXT"])

# Source TR IDs (used as market discriminator fallback)
_SOURCE_TR_IDS = st.sampled_from(["H0STCNT0", "H0NXCNT0"])

# 6-digit HHMMSS trade time (09:00:00 – 15:30:00 KST range, but any 6-digit is valid for the key)
_TRADE_TIME = st.from_regex(r"[0-9]{6}", fullmatch=True)

# 8-digit YYYYMMDD business date
_BUSINESS_DATE = st.from_regex(r"20[2-3][0-9](0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])", fullmatch=True)

# Symbol: zero-preserving 6-digit stock code
_SYMBOL = st.from_regex(r"[0-9]{6}", fullmatch=True)

# Cumulative volume: non-negative integer
_CUMULATIVE_VOLUME = st.integers(min_value=0, max_value=10_000_000_000)

# Price: positive integer (KRW, no decimals)
_PRICE = st.integers(min_value=1, max_value=10_000_000)

# Trade type: KIS CCLD_DVSN code (trimmed string, preserve as-is)
_TRADE_TYPE = st.sampled_from(["1", "2", "3", "4", "5", " ", ""])


def _make_parsed_tick(
    market: str,
    symbol: str,
    business_date: str,
    cumulative_volume: int,
    trade_time: str,
    price: int,
    trade_type: str,
) -> ParsedTick:
    """Build a minimal ParsedTick with all required fields populated."""
    # Non-identity fields are fixed constants — they must NOT affect event_id
    return ParsedTick(
        source_tr_id="H0STCNT0",
        market=market,
        received_at="2026-01-01T09:00:00Z",
        symbol=symbol,
        trade_time=trade_time,
        price=price,
        change_sign="2",
        change=0,
        change_rate="0.00",
        vwap="0.00",
        open=price,
        high=price,
        low=price,
        ask_price_1=price,
        bid_price_1=price,
        trade_volume=1,
        cumulative_volume=cumulative_volume,
        cumulative_amount=price,
        sell_count=0,
        buy_count=0,
        net_buy_count=0,
        trade_strength="50.00",
        total_sell_volume=0,
        total_buy_volume=0,
        trade_type=trade_type,
        buy_ratio="0.00",
        prev_day_volume_rate="0.00",
        open_time="090000",
        open_vs_sign="2",
        open_vs_price=0,
        high_time="090000",
        high_vs_sign="2",
        high_vs_price=0,
        low_time="090000",
        low_vs_sign="2",
        low_vs_price=0,
        business_date=business_date,
        market_session_code="1",
        trading_halted="0",
        ask_remain_1=0,
        bid_remain_1=0,
        total_ask_remain=0,
        total_bid_remain=0,
        volume_turnover="0.00",
        prev_same_hour_volume=0,
        prev_same_hour_volume_rate="0.00",
        hour_class_code="0",
        market_termination_code="0",
        vi_trigger_price=0,
    )


@st.composite
def stock_tick_strategy(draw: st.DrawFn) -> ParsedTick:
    """Hypothesis strategy producing a valid ParsedTick for property tests."""
    return _make_parsed_tick(
        market=draw(_MARKETS),
        symbol=draw(_SYMBOL),
        business_date=draw(_BUSINESS_DATE),
        cumulative_volume=draw(_CUMULATIVE_VOLUME),
        trade_time=draw(_TRADE_TIME),
        price=draw(_PRICE),
        trade_type=draw(_TRADE_TYPE),
    )


# ---------------------------------------------------------------------------
# I5 — Determinism: same tick → same event_id, always
# ---------------------------------------------------------------------------

@given(tick=stock_tick_strategy())
@settings(max_examples=200)
def test_i5_determinism_same_call(tick: ParsedTick) -> None:
    """
    I5 (determinism): calling compute_event_id twice on the same tick
    must return the same UUID string.

    Invariant: compute_event_id is a pure function of the identity fields.
    No randomness, no wall-clock, no shared mutable state.
    """
    id_first = compute_event_id(tick)
    id_second = compute_event_id(tick)
    assert id_first == id_second, (
        f"compute_event_id is not deterministic: "
        f"first={id_first!r}, second={id_second!r} for tick={tick!r}"
    )


@given(tick=stock_tick_strategy())
@settings(max_examples=200)
def test_i5_determinism_simulated_restart(tick: ParsedTick) -> None:
    """
    I5 (determinism after restart): compute_event_id must produce the same
    result even when called in a 'fresh' context (no shared state between calls).

    Simulated by deep-copying the tick to ensure no object identity is shared,
    then calling compute_event_id on both copies.
    """
    tick_copy = copy.deepcopy(tick)
    id_original = compute_event_id(tick)
    id_after_restart = compute_event_id(tick_copy)
    assert id_original == id_after_restart, (
        f"compute_event_id differs after simulated restart: "
        f"original={id_original!r}, after_restart={id_after_restart!r}"
    )


# ---------------------------------------------------------------------------
# Injectivity: ticks differing in ONE identity field → different event_ids
# ---------------------------------------------------------------------------

@given(
    tick=stock_tick_strategy(),
    other_market=_MARKETS,
)
@settings(max_examples=100)
def test_injectivity_market(tick: ParsedTick, other_market: str) -> None:
    """
    Injectivity on `market`: if market differs, event_ids must differ.

    KRX and NXT have separate cumulative volume counters; same
    (symbol, date, cumvol, time, price, trade_type) in different markets
    must NOT collide.
    """
    assume(other_market != tick.market)

    tick_other = _make_parsed_tick(
        market=other_market,
        symbol=tick.symbol,
        business_date=tick.business_date,
        cumulative_volume=tick.cumulative_volume,
        trade_time=tick.trade_time,
        price=tick.price,
        trade_type=tick.trade_type,
    )
    assert compute_event_id(tick) != compute_event_id(tick_other), (
        f"Collision on market change: {tick.market!r} vs {other_market!r}"
    )


@given(
    tick=stock_tick_strategy(),
    other_symbol=_SYMBOL,
)
@settings(max_examples=100)
def test_injectivity_symbol(tick: ParsedTick, other_symbol: str) -> None:
    """Injectivity on `symbol`: different symbols → different event_ids."""
    assume(other_symbol != tick.symbol)

    tick_other = _make_parsed_tick(
        market=tick.market,
        symbol=other_symbol,
        business_date=tick.business_date,
        cumulative_volume=tick.cumulative_volume,
        trade_time=tick.trade_time,
        price=tick.price,
        trade_type=tick.trade_type,
    )
    assert compute_event_id(tick) != compute_event_id(tick_other), (
        f"Collision on symbol change: {tick.symbol!r} vs {other_symbol!r}"
    )


@given(
    tick=stock_tick_strategy(),
    other_date=_BUSINESS_DATE,
)
@settings(max_examples=100)
def test_injectivity_business_date(tick: ParsedTick, other_date: str) -> None:
    """Injectivity on `business_date`: different dates → different event_ids."""
    assume(other_date != tick.business_date)

    tick_other = _make_parsed_tick(
        market=tick.market,
        symbol=tick.symbol,
        business_date=other_date,
        cumulative_volume=tick.cumulative_volume,
        trade_time=tick.trade_time,
        price=tick.price,
        trade_type=tick.trade_type,
    )
    assert compute_event_id(tick) != compute_event_id(tick_other), (
        f"Collision on business_date change: {tick.business_date!r} vs {other_date!r}"
    )


@given(
    tick=stock_tick_strategy(),
    other_cumvol=_CUMULATIVE_VOLUME,
)
@settings(max_examples=100)
def test_injectivity_cumulative_volume(tick: ParsedTick, other_cumvol: int) -> None:
    """Injectivity on `cumulative_volume`: different values → different event_ids."""
    assume(other_cumvol != tick.cumulative_volume)

    tick_other = _make_parsed_tick(
        market=tick.market,
        symbol=tick.symbol,
        business_date=tick.business_date,
        cumulative_volume=other_cumvol,
        trade_time=tick.trade_time,
        price=tick.price,
        trade_type=tick.trade_type,
    )
    assert compute_event_id(tick) != compute_event_id(tick_other), (
        f"Collision on cumulative_volume change: {tick.cumulative_volume!r} vs {other_cumvol!r}"
    )


# ---------------------------------------------------------------------------
# Non-identity fields: changing them must NOT change event_id
# ---------------------------------------------------------------------------

@given(tick=stock_tick_strategy())
@settings(max_examples=100)
def test_non_identity_received_at_ignored(tick: ParsedTick) -> None:
    """
    Non-identity field `received_at` must NOT affect event_id.

    received_at is producer wall-clock time — it changes on replay/retry
    and must be excluded from the identity key.
    """
    tick_other = _make_parsed_tick(
        market=tick.market,
        symbol=tick.symbol,
        business_date=tick.business_date,
        cumulative_volume=tick.cumulative_volume,
        trade_time=tick.trade_time,
        price=tick.price,
        trade_type=tick.trade_type,
    )
    # Mutate received_at on the copy (non-identity field)
    tick_other_dict = tick_other.model_dump()
    tick_other_dict["received_at"] = "2099-12-31T23:59:59Z"
    tick_other_mutated = ParsedTick(**tick_other_dict)

    assert compute_event_id(tick) == compute_event_id(tick_other_mutated), (
        "received_at (non-identity) must not affect event_id"
    )

"""Pure 5-minute OHLC aggregation for stock tick dictionaries.

``trade_volume`` is treated as per-tick executed volume and is summed inside the
bar. ``cumulative_volume`` is intentionally ignored because it is not the bucket
volume delta.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
BUCKET_SIZE = timedelta(minutes=5)


@dataclass(frozen=True)
class _TickObservation:
    sort_key: tuple[datetime, tuple[Any, ...]]
    price: int
    volume: int
    vwap: Decimal | None


@dataclass(frozen=True)
class _BaseBar:
    open: int
    high: int
    low: int
    close: int
    volume: int
    vwap_last: Decimal | None
    tick_count: int


@dataclass
class BarState:
    open: int
    high: int
    low: int
    close: int
    volume: int
    vwap_last: Decimal | None
    tick_count: int
    is_final: bool = False
    _observations: dict[tuple[Any, ...], _TickObservation] = field(default_factory=dict, repr=False, compare=False)
    _base: _BaseBar | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_existing(
        cls,
        *,
        open: int,
        high: int,
        low: int,
        close: int,
        volume: int,
        vwap: Decimal | None,
        tick_count: int,
        is_final: bool = False,
    ) -> BarState:
        """Hydrate a state from a persisted bar for restart-mid-bucket recovery."""
        bar = cls(
            open=open,
            high=high,
            low=low,
            close=close,
            volume=volume,
            vwap_last=vwap,
            tick_count=tick_count,
            is_final=is_final,
        )
        bar._base = _BaseBar(open, high, low, close, volume, vwap, tick_count)
        bar._ensure_invariants()
        return bar

    def _ensure_invariants(self) -> None:
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError("invalid OHLC invariant: expected low <= open/close <= high")


class FiveMinuteAggregator:
    def __init__(self) -> None:
        self._bars: dict[tuple[str, datetime], BarState] = {}
        self._latest_bucket_by_symbol: dict[str, datetime] = {}
        self._newly_finalized: list[tuple[str, datetime, BarState]] = []

    @staticmethod
    def bucket_start(business_date: str, trade_time: str) -> datetime:
        trade_at = _parse_trade_datetime(business_date, trade_time)
        floored_minute = trade_at.minute - (trade_at.minute % 5)
        return trade_at.replace(minute=floored_minute, second=0, microsecond=0)

    def add_tick(self, tick: dict[str, Any]) -> tuple[datetime, BarState]:
        """Add one tick and return ``(bucket_start, current BarState)``.

        OHLC is derived by trade-time ordering so replaying the same tick set in
        any order yields the same bar. Exact duplicate tick dictionaries are
        ignored, making Kafka replay of already-seen ticks idempotent at this
        pure-logic layer.
        """
        symbol = _required_text(tick, "symbol")
        bucket = self.bucket_start(_required_text(tick, "business_date"), _required_text(tick, "trade_time"))
        key = (symbol, bucket)

        self._mark_prior_buckets_final(symbol, bucket)

        bar = self._bars.get(key)
        if bar is None:
            bar = self._new_bar_for_tick(tick, is_final=self._is_older_than_latest(symbol, bucket))
            self._bars[key] = bar
        else:
            self._add_observation(bar, tick)

        bar.is_final = bar.is_final or self._is_older_than_latest(symbol, bucket)
        self._latest_bucket_by_symbol[symbol] = max(bucket, self._latest_bucket_by_symbol.get(symbol, bucket))
        return bucket, bar

    def hydrate(self, symbol: str, bucket_start: datetime, bar: BarState) -> None:
        bucket = _as_kst(bucket_start)
        self._bars[(symbol, bucket)] = bar
        if not bar.is_final:
            self._latest_bucket_by_symbol[symbol] = max(bucket, self._latest_bucket_by_symbol.get(symbol, bucket))

    def current_bar(self, symbol: str) -> BarState | None:
        bucket = self._latest_bucket_by_symbol.get(symbol)
        if bucket is None:
            return None
        return self._bars.get((symbol, bucket))

    def pop_finalized_bars(self) -> list[tuple[str, datetime, BarState]]:
        finalized = list(self._newly_finalized)
        self._newly_finalized.clear()
        return finalized

    def finalized_bars(self, now: datetime) -> list[tuple[str, datetime, BarState]]:
        now_kst = _as_kst(now)
        before = len(self._newly_finalized)
        for (symbol, bucket), bar in self._bars.items():
            if not bar.is_final and bucket + BUCKET_SIZE <= now_kst:
                self._finalize(symbol, bucket, bar)
        return self._newly_finalized[before:]

    def _new_bar_for_tick(self, tick: dict[str, Any], *, is_final: bool) -> BarState:
        price = _required_int(tick, "price")
        volume = _required_int(tick, "trade_volume")
        bar = BarState(
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            vwap_last=_optional_decimal(tick.get("vwap")),
            tick_count=1,
            is_final=is_final,
        )
        self._add_observation(bar, tick)
        return bar

    def _add_observation(self, bar: BarState, tick: dict[str, Any]) -> None:
        fingerprint = _fingerprint(tick)
        if fingerprint in bar._observations:
            return

        trade_at = _parse_trade_datetime(_required_text(tick, "business_date"), _required_text(tick, "trade_time"))
        bar._observations[fingerprint] = _TickObservation(
            sort_key=(trade_at, fingerprint),
            price=_required_int(tick, "price"),
            volume=_required_int(tick, "trade_volume"),
            vwap=_optional_decimal(tick.get("vwap")),
        )
        _recompute(bar)

    def _mark_prior_buckets_final(self, symbol: str, bucket: datetime) -> None:
        for (bar_symbol, bar_bucket), bar in list(self._bars.items()):
            if bar_symbol == symbol and bar_bucket < bucket and not bar.is_final:
                self._finalize(bar_symbol, bar_bucket, bar)

    def _finalize(self, symbol: str, bucket: datetime, bar: BarState) -> None:
        bar.is_final = True
        self._newly_finalized.append((symbol, bucket, bar))

    def _is_older_than_latest(self, symbol: str, bucket: datetime) -> bool:
        latest = self._latest_bucket_by_symbol.get(symbol)
        return latest is not None and bucket < latest


def _recompute(bar: BarState) -> None:
    observations = sorted(bar._observations.values(), key=lambda item: item.sort_key)
    if bar._base is None:
        prices = [item.price for item in observations]
        first = observations[0]
        last = observations[-1]
        bar.open = first.price
        bar.high = max(prices)
        bar.low = min(prices)
        bar.close = last.price
        bar.volume = sum(item.volume for item in observations)
        bar.vwap_last = last.vwap
        bar.tick_count = len(observations)
    else:
        base = bar._base
        if observations:
            prices = [item.price for item in observations]
            last = observations[-1]
            bar.high = max(base.high, *prices)
            bar.low = min(base.low, *prices)
            bar.close = last.price
            bar.volume = base.volume + sum(item.volume for item in observations)
            bar.vwap_last = last.vwap
            bar.tick_count = base.tick_count + len(observations)
        else:
            bar.open = base.open
            bar.high = base.high
            bar.low = base.low
            bar.close = base.close
            bar.volume = base.volume
            bar.vwap_last = base.vwap_last
            bar.tick_count = base.tick_count
    bar._ensure_invariants()


def _parse_trade_datetime(business_date: str, trade_time: str) -> datetime:
    return datetime.strptime(f"{business_date}{trade_time}", "%Y%m%d%H%M%S").replace(tzinfo=KST)


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def _required_text(tick: dict[str, Any], key: str) -> str:
    value = tick[key]
    if value is None:
        raise ValueError(f"tick field {key!r} is required")
    return str(value)


def _required_int(tick: dict[str, Any], key: str) -> int:
    value = tick[key]
    if value is None:
        raise ValueError(f"tick field {key!r} is required")
    return int(value)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _fingerprint(value: Any) -> tuple[Any, ...]:
    if isinstance(value, dict):
        return tuple((key, _fingerprint(item)) for key, item in sorted(value.items()))
    if isinstance(value, list | tuple):
        return tuple(_fingerprint(item) for item in value)
    if isinstance(value, Decimal):
        return ("Decimal", str(value))
    return (value,)

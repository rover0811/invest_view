"""Pure 5-minute OHLC aggregation for stock tick dictionaries.

``trade_volume`` is treated as per-tick executed volume and is summed inside the
bar. ``cumulative_volume`` is intentionally ignored because it is not the bucket
volume delta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
BUCKET_SIZE = timedelta(minutes=5)
TickKey = tuple[datetime, int, int]


@dataclass
class BarState:
    open: int
    high: int
    low: int
    close: int
    volume: int
    vwap_last: Decimal | None
    tick_count: int
    open_key: TickKey
    close_key: TickKey
    is_final: bool = False

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
        open_key: TickKey,
        close_key: TickKey,
        is_final: bool = False,
    ) -> BarState:
        """Hydrate a state from a complete source that preserved order keys."""
        bar = cls(
            open=open,
            high=high,
            low=low,
            close=close,
            volume=volume,
            vwap_last=vwap,
            tick_count=tick_count,
            open_key=_normalize_tick_key(open_key),
            close_key=_normalize_tick_key(close_key),
            is_final=is_final,
        )
        bar._ensure_invariants()
        return bar

    @classmethod
    def from_tick(
        cls,
        *,
        price: int,
        volume: int,
        vwap: Decimal | None,
        tick_key: TickKey,
        is_final: bool = False,
    ) -> BarState:
        key = _normalize_tick_key(tick_key)
        return cls(
            open=price,
            high=price,
            low=price,
            close=price,
            volume=volume,
            vwap_last=vwap,
            tick_count=1,
            open_key=key,
            close_key=key,
            is_final=is_final,
        )

    def add_tick(self, *, price: int, volume: int, vwap: Decimal | None, tick_key: TickKey) -> None:
        key = _normalize_tick_key(tick_key)
        if key < self.open_key:
            self.open = price
            self.open_key = key
        if key >= self.close_key:
            self.close = price
            self.close_key = key
            self.vwap_last = vwap
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.volume += volume
        self.tick_count += 1
        self._ensure_invariants()

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

    def add_tick(
        self,
        tick: dict[str, Any],
        *,
        event_ts: datetime,
        partition: int,
        offset: int,
    ) -> tuple[datetime, BarState]:
        """Add one tick and return ``(bucket_start, current BarState)``.

        The caller must pass only ticks that were newly inserted into bronze in the
        current transaction. This aggregator performs no duplicate suppression;
        volume/tick_count are incremented exactly once per call. Open and close are
        deterministic by ``(event_ts, partition, offset)`` so arrival order does not
        affect the bar.
        """
        symbol = _required_text(tick, "symbol")
        bucket = self.bucket_start(_required_text(tick, "business_date"), _required_text(tick, "trade_time"))
        key = (symbol, bucket)
        tick_key = (_as_kst(event_ts), int(partition), int(offset))
        is_older_than_latest = self._is_older_than_latest(symbol, bucket)

        self._mark_prior_buckets_final(symbol, bucket)

        bar = self._bars.get(key)
        if bar is None:
            bar = self._new_bar_for_tick(tick, tick_key=tick_key, is_final=is_older_than_latest)
            self._bars[key] = bar
        else:
            bar.add_tick(
                price=_required_int(tick, "price"),
                volume=_required_int(tick, "trade_volume"),
                vwap=_optional_decimal(tick.get("vwap")),
                tick_key=tick_key,
            )

        self._latest_bucket_by_symbol[symbol] = max(bucket, self._latest_bucket_by_symbol.get(symbol, bucket))
        if is_older_than_latest or bar.is_final:
            self._finalize(symbol, bucket, bar)
        return bucket, bar

    def hydrate(self, symbol: str, bucket_start: datetime, bar: BarState) -> None:
        bucket = _as_kst(bucket_start)
        if self._is_older_than_latest(symbol, bucket):
            bar.is_final = True
        self._bars[(symbol, bucket)] = bar
        if not bar.is_final:
            self._latest_bucket_by_symbol[symbol] = max(bucket, self._latest_bucket_by_symbol.get(symbol, bucket))

    def has_bar(self, symbol: str, bucket_start: datetime) -> bool:
        return (symbol, _as_kst(bucket_start)) in self._bars

    def current_bar(self, symbol: str) -> BarState | None:
        bucket = self._latest_bucket_by_symbol.get(symbol)
        if bucket is None:
            return None
        return self._bars.get((symbol, bucket))

    def pop_finalized_bars(self) -> list[tuple[str, datetime, BarState]]:
        finalized = list(self._newly_finalized)
        self._newly_finalized.clear()
        return finalized

    def clear(self) -> None:
        self._bars.clear()
        self._latest_bucket_by_symbol.clear()
        self._newly_finalized.clear()

    def finalized_bars(self, now: datetime) -> list[tuple[str, datetime, BarState]]:
        now_kst = _as_kst(now)
        before = len(self._newly_finalized)
        for (symbol, bucket), bar in list(self._bars.items()):
            if not bar.is_final and bucket + BUCKET_SIZE <= now_kst:
                self._finalize(symbol, bucket, bar)
        return self._newly_finalized[before:]

    def _new_bar_for_tick(self, tick: dict[str, Any], *, tick_key: TickKey, is_final: bool) -> BarState:
        price = _required_int(tick, "price")
        volume = _required_int(tick, "trade_volume")
        bar = BarState.from_tick(
            price=price,
            volume=volume,
            vwap=_optional_decimal(tick.get("vwap")),
            tick_key=tick_key,
            is_final=is_final,
        )
        return bar

    def _mark_prior_buckets_final(self, symbol: str, bucket: datetime) -> None:
        for (bar_symbol, bar_bucket), bar in list(self._bars.items()):
            if bar_symbol == symbol and bar_bucket < bucket and not bar.is_final:
                self._finalize(bar_symbol, bar_bucket, bar)

    def _finalize(self, symbol: str, bucket: datetime, bar: BarState) -> None:
        bar.is_final = True
        self._newly_finalized.append((symbol, bucket, bar))
        if self._bars.get((symbol, bucket)) is bar:
            del self._bars[(symbol, bucket)]

    def _is_older_than_latest(self, symbol: str, bucket: datetime) -> bool:
        latest = self._latest_bucket_by_symbol.get(symbol)
        return latest is not None and bucket < latest


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


def _normalize_tick_key(key: TickKey) -> TickKey:
    event_ts, partition, offset = key
    return (_as_kst(event_ts), int(partition), int(offset))

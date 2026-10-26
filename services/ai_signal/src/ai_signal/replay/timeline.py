"""Merge bar ticks and news records into one event-time ordered stream."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ai_signal.replay.bars import KST

EventKind = Literal["tick", "news"]
_KIND_ORDER = {"news": 0, "tick": 1}


@dataclass(frozen=True)
class ReplayEvent:
    time: datetime
    kind: EventKind
    payload: Mapping[str, object]


def tick_time(tick: Mapping[str, object]) -> datetime:
    stamp = f"{tick['business_date']}{tick['trade_time']}"
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=KST)


def news_time(news: Mapping[str, object]) -> datetime:
    """``published_at`` is timestamp-millis (news-raw) or an aware datetime."""
    value = news["published_at"]
    if isinstance(value, datetime):
        return value.astimezone(KST)
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, tz=KST)
    raise TypeError(f"unsupported published_at: {value!r}")


def merge_events(
    bars_ticks: Iterable[Mapping[str, object]],
    news: Iterable[Mapping[str, object]],
) -> list[ReplayEvent]:
    """Order by event time; news sorts before a tick of the same minute.

    News published before the first tick is emitted at the first step; its
    payload keeps its own ``published_at``.
    """
    ticks = [ReplayEvent(tick_time(t), "tick", t) for t in bars_ticks]
    first = min((e.time for e in ticks), default=None)
    events = list(ticks)
    for item in news:
        at = news_time(item)
        if first is not None and at < first:
            at = first
        events.append(ReplayEvent(at, "news", item))

    def key(event: ReplayEvent) -> tuple[datetime, int, datetime]:
        minute = event.time.replace(second=0, microsecond=0)
        return (minute, _KIND_ORDER[event.kind], event.time)

    return sorted(events, key=key)

"""Signal request emission shared by the document processor and the move sink.

``request_targets`` is the single place that decides which symbols receive a
``signal-requests`` message (18.10, 16.6). Phase 1 returns the triggering
symbols only; phase 2 widens MARKET scope triggers to symbols with an open move
inside this function, which is why it already receives the session.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_signal.outbox.writer import OutboxWriter

SIGNAL_REQUESTS_TOPIC = "signal-requests"

Scope = Literal["MARKET", "SECTOR", "SYMBOL"]


class SignalRequestReason(StrEnum):
    PRICE_STATE_CHANGE = "PRICE_STATE_CHANGE"
    NEW_DOCUMENT = "NEW_DOCUMENT"
    FLOW_UPDATE = "FLOW_UPDATE"
    TARGET_PRICE_UPDATE = "TARGET_PRICE_UPDATE"
    MARKET_CLOSE = "MARKET_CLOSE"


@dataclass(frozen=True)
class RequestTrigger:
    scope: Scope
    symbols: tuple[str, ...]


def _unique(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(symbol for symbol in symbols if symbol))


async def request_targets(
    session: AsyncSession, trigger: RequestTrigger
) -> tuple[str, ...]:
    _ = session
    return _unique(trigger.symbols)


async def publish_signal_requests(
    outbox: OutboxWriter,
    symbols: Sequence[str],
    reason: SignalRequestReason,
    event_time: datetime,
) -> tuple[UUID, ...]:
    event_ids: list[UUID] = []
    for symbol in _unique(symbols):
        event_ids.append(
            await outbox.write(
                topic=SIGNAL_REQUESTS_TOPIC,
                key=symbol,
                value={
                    "symbol": symbol,
                    "reason": reason.value,
                    "event_time": event_time,
                },
            )
        )
    return tuple(event_ids)

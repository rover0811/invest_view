"""Signal worker: ``Handler`` for ``signal-requests`` (12, 16, 18.10 "시그널").

For the requested symbol the worker reads the latest move, skips it unless the
symbol is ``signal_enabled``, collects candidates in the move's candidate range,
generates the signal and writes a new ``gold.signal`` version together with a
``signal-updates`` outbox event. A request whose generation input equals the
latest stored version is a no-op, so redelivery and duplicate requests do not
call the LLM again.

Regeneration rules: candidates are bounded by the request ``event_time`` (no
look-ahead). A NEUTRAL move shows no cause, so PRICE_STATE_CHANGE writes HIDDEN
without a model call and other reasons are ignored. NEW_DOCUMENT requests whose
event time is within ``COALESCE_WINDOW`` after the event time of the latest
signal of the same move version are deferred; the next later request picks up
every candidate. When every model step failed a ``gold.signal_retry`` row is
written; only PRICE_STATE_CHANGE then writes HIDDEN, other reasons keep the
previous version because its text still describes the current state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_signal.consumers.frame import ConsumedMessage, HandlerContext
from ai_signal.llm.container import shared_llm
from ai_signal.signal_requests import SignalRequestReason
from ai_signal.signals.candidates import (
    CANDIDATE_SOURCES,
    CandidateSource,
    collect_candidates,
)
from ai_signal.signals.generation import (
    HIDDEN,
    Generation,
    GenerationRequest,
    SignalGenerator,
)
from ai_signal.signals.move import (
    CandidateRange,
    candidate_range,
    latest_move,
    locked_move_version,
)
from ai_signal.signals.store import (
    clear_retry,
    insert_signal,
    latest_signal,
    next_signal_version,
    publish_update,
    record_retry,
    set_move_status,
)

logger = logging.getLogger(__name__)

SIGNAL_DLQ_TOPIC = "signal-dlq"
COALESCE_WINDOW = timedelta(minutes=10)
NEUTRAL = "NEUTRAL"


def _event_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, UTC)
    raise ValueError(f"invalid event_time {value!r}")


@dataclass(frozen=True)
class SignalRequest:
    symbol: str
    reason: SignalRequestReason
    event_time: datetime

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> SignalRequest:
        return cls(
            str(value["symbol"]),
            SignalRequestReason(str(value["reason"])),
            _event_time(value["event_time"]),
        )

    @property
    def is_state_change(self) -> bool:
        return self.reason is SignalRequestReason.PRICE_STATE_CHANGE


def default_generator() -> SignalGenerator:
    return SignalGenerator.from_llm(shared_llm())


def utc_now() -> datetime:
    return datetime.now(UTC)


class SignalWorker:
    def __init__(
        self,
        generator_factory: Callable[[], SignalGenerator] = default_generator,
        sources: Sequence[CandidateSource] = CANDIDATE_SOURCES,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._generator_factory = generator_factory
        self._generator: SignalGenerator | None = None
        self._sources = tuple(sources)
        self._clock = clock

    def _get_generator(self) -> SignalGenerator:
        if self._generator is None:
            self._generator = self._generator_factory()
        return self._generator

    async def __call__(self, message: ConsumedMessage, context: HandlerContext) -> None:
        session = context.session
        request = SignalRequest.from_value(message.value)
        move = await latest_move(session, request.symbol)
        if move is None:
            logger.info(
                "no move for %s; skip %s request", request.symbol, request.reason
            )
            return
        if not move.signal_enabled:
            return
        neutral = move.price_state == NEUTRAL
        if neutral and not request.is_state_change:
            logger.info("skip %s %s: move is NEUTRAL", request.symbol, request.reason)
            return
        latest = await latest_signal(session, move.move_id)
        if (
            not request.is_state_change
            and latest is not None
            and latest.move_version == move.version
            and latest.as_of_event_time is not None
            and request.event_time < latest.as_of_event_time + COALESCE_WINDOW
        ):
            logger.info(
                "defer %s %s at %s: signal v%d covers events up to %s",
                request.symbol,
                request.reason,
                request.event_time.isoformat(),
                latest.version,
                latest.as_of_event_time.isoformat(),
            )
            return
        full = candidate_range(move)
        window = CandidateRange(full.start, min(full.end, request.event_time))
        candidates = await collect_candidates(session, move, window, self._sources)
        generation_request = GenerationRequest(move, candidates)
        generator = self._get_generator()
        input_hash = generation_request.input_hash(generator.prompt_version)
        if latest is not None and latest.input_hash == input_hash:
            logger.info(
                "skip %s move %s: input unchanged since signal v%d",
                request.symbol,
                move.move_id,
                latest.version,
            )
            return

        generation = (
            Generation(HIDDEN, None, None, generator.prompt_version, None)
            if neutral
            else await generator.generate(generation_request)
        )

        if await locked_move_version(session, move.move_id) != move.version:
            logger.info(
                "move %s changed during generation; a newer request follows",
                move.move_id,
            )
            return
        now = self._clock()
        if generation.llm_failed and generation.last_error is not None:
            await record_retry(session, move, generation.last_error, now)
            if not request.is_state_change:
                logger.info(
                    "keep previous signal of %s move %s: %s generation failed",
                    request.symbol,
                    move.move_id,
                    request.reason,
                )
                return
        version = await next_signal_version(session, move.move_id)
        await insert_signal(
            session, move, version, generation, input_hash, now, request.event_time
        )
        await set_move_status(session, move, generation.decision.status)
        if generation.decision.status == "SHOWN":
            await clear_retry(session, move)
        _ = await publish_update(context.outbox, move, version, generation, now)
        logger.info(
            "signal %s move %s v%d (move v%d): %s",
            request.symbol,
            move.move_id,
            version,
            move.version,
            generation.decision.status,
        )

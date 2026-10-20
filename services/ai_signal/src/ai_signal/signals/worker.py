"""Signal worker: ``Handler`` for ``signal-requests`` (12, 16, 18.10 "시그널").

For the requested symbol the worker reads the latest move, skips it unless the
symbol is ``signal_enabled``, collects candidates in the move's candidate range,
generates the signal and writes a new ``gold.signal`` version together with a
``signal-updates`` outbox event. A request whose generation input equals the
latest stored version is a no-op, so redelivery and duplicate requests do not
call the LLM again. When every model step failed the result is HIDDEN and a
``gold.signal_retry`` row is written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ai_signal.consumers.frame import ConsumedMessage, HandlerContext
from ai_signal.llm import shared_client
from ai_signal.signal_requests import SignalRequestReason
from ai_signal.signals.candidates import (
    CANDIDATE_SOURCES,
    CandidateSource,
    collect_candidates,
)
from ai_signal.signals.generation import GenerationRequest, SignalGenerator
from ai_signal.signals.move import candidate_range, latest_move, locked_move_version
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


@dataclass(frozen=True)
class SignalRequest:
    symbol: str
    reason: SignalRequestReason

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> SignalRequest:
        return cls(str(value["symbol"]), SignalRequestReason(str(value["reason"])))


def default_generator() -> SignalGenerator:
    return SignalGenerator.from_client(shared_client())


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
        window = candidate_range(move)
        candidates = await collect_candidates(session, move, window, self._sources)
        generation_request = GenerationRequest(move, candidates)
        generator = self._get_generator()
        input_hash = generation_request.input_hash(generator.prompt_version)
        latest = await latest_signal(session, move.move_id)
        if latest is not None and latest.input_hash == input_hash:
            logger.info(
                "skip %s move %s: input unchanged since signal v%d",
                request.symbol,
                move.move_id,
                latest.version,
            )
            return

        generation = await generator.generate(generation_request)

        if await locked_move_version(session, move.move_id) != move.version:
            logger.info(
                "move %s changed during generation; a newer request follows",
                move.move_id,
            )
            return
        now = self._clock()
        version = await next_signal_version(session, move.move_id)
        await insert_signal(session, move, version, generation, input_hash, now)
        await set_move_status(session, move, generation.decision.status)
        if generation.llm_failed and generation.last_error is not None:
            await record_retry(session, move, generation.last_error, now)
        elif generation.decision.status == "SHOWN":
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

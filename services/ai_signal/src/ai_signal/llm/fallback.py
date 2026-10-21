"""Fallback as an ordered list of steps (12.4, 18.10 "시그널").

Every step takes the same input and either returns a result or passes (``None``).
``run_fallback`` stops at the first result. Phase 1 lists are primary model,
secondary model, then the caller's terminal behaviour (``HIDDEN`` for the signal
worker, "not judged" for the document processor). Phase 2 inserts flow and
decomposition steps into the list without changing ``run_fallback``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from ai_signal.llm.port import LlmError, LlmErrorKind
from ai_signal.llm.routing import Llm, Route

logger = logging.getLogger(__name__)

In_contra = TypeVar("In_contra", contravariant=True)
Out = TypeVar("Out")
In_ = TypeVar("In_")
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class StepResult(Generic[Out]):
    value: Out
    decided_by: str
    model_version: str | None = None


@dataclass(frozen=True)
class LlmAttempt:
    """One model call kept for audit; ``error`` is ``None`` when the answer was used."""

    step: str
    model: str
    error: LlmErrorKind | None
    raw_content: str | None
    adapter: str | None = None

    def as_json(self) -> dict[str, str | None]:
        return {
            "step": self.step,
            "adapter": self.adapter,
            "model": self.model,
            "error": self.error.value if self.error is not None else None,
            "raw_content": self.raw_content,
        }


@dataclass
class StepFailures:
    errors: list[tuple[str, LlmErrorKind]] = field(default_factory=list)
    attempts: list[LlmAttempt] = field(default_factory=list)

    @property
    def last_error(self) -> LlmErrorKind | None:
        return self.errors[-1][1] if self.errors else None

    def reject_last_attempt(self, step: str, kind: LlmErrorKind) -> None:
        """Record that ``step`` returned an answer the caller could not use."""
        self.errors.append((step, kind))
        if self.attempts and self.attempts[-1].step == step:
            last = self.attempts[-1]
            self.attempts[-1] = LlmAttempt(
                last.step, last.model, kind, last.raw_content, last.adapter
            )


class FallbackStep(Protocol[In_contra, Out]):
    @property
    def name(self) -> str: ...

    async def attempt(
        self, request: In_contra, failures: StepFailures
    ) -> StepResult[Out] | None: ...


@dataclass(frozen=True)
class FallbackOutcome(Generic[Out]):
    result: StepResult[Out] | None
    failures: StepFailures


async def run_fallback(
    steps: Sequence[FallbackStep[In_, Out]], request: In_
) -> FallbackOutcome[Out]:
    failures = StepFailures()
    for step in steps:
        result = await step.attempt(request, failures)
        if result is not None:
            return FallbackOutcome(result=result, failures=failures)
    return FallbackOutcome(result=None, failures=failures)


MessageBuilder = Callable[[In_], list[dict[str, str]]]


def route_step_names(routes: Sequence[Route]) -> list[str]:
    """``primary`` and ``secondary`` keep the audit names of the two-model setup."""
    names = ["primary", "secondary"]
    return [names[i] if i < len(names) else f"fallback{i}" for i in range(len(routes))]


@dataclass(frozen=True)
class ModelStep(Generic[In_, T]):
    """Calls one route; an ``LlmError`` or an unhealthy route is recorded and the step passes."""

    name: str
    llm: Llm
    route: Route
    output_type: type[T]
    messages: MessageBuilder[In_]
    decided_by: str = "llm"

    def _fail(self, failures: StepFailures, kind: LlmErrorKind, raw: str | None) -> None:
        failures.errors.append((self.name, kind))
        failures.attempts.append(
            LlmAttempt(self.name, self.route.model, kind, raw, self.route.adapter_name)
        )

    async def attempt(
        self, request: In_, failures: StepFailures
    ) -> StepResult[T] | None:
        blocked = self.llm.health.blocked(self.route)
        if blocked is not None:
            logger.info("%s step skipped: %s is %s", self.name, self.route, blocked)
            self._fail(failures, blocked, None)
            return None
        try:
            completion = await self.llm.port(self.route).complete(
                self.llm.request(self.route, self.messages(request)), self.output_type
            )
        except LlmError as exc:
            logger.warning("%s step passed: %s", self.name, exc)
            self.llm.health.record_failure(self.route, exc)
            self._fail(failures, exc.kind, exc.raw_content)
            return None
        self.llm.health.record_success(self.route)
        failures.attempts.append(
            LlmAttempt(
                self.name,
                self.route.model,
                None,
                completion.raw_content,
                self.route.adapter_name,
            )
        )
        return StepResult(
            value=completion.output,
            decided_by=self.decided_by,
            model_version=completion.model_version,
        )

"""Routes per purpose and their health (12.4).

``AI_SIGNAL_LLM_ROUTES_<PURPOSE>`` lists ``adapter:model`` pairs separated by
commas; only the first colon splits, so ``openrouter:nvidia/x:free`` keeps the
``:free`` suffix in the model. A route whose daily quota is exhausted is skipped
until the reset time; repeated UNAVAILABLE or 5xx failures open a short breaker.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ai_signal.llm.port import (
    CompletionRequest,
    LlmError,
    LlmErrorKind,
    LlmPort,
    ReasoningEffort,
)

ROUTES_ENV_PREFIX = "AI_SIGNAL_LLM_ROUTES_"


@dataclass(frozen=True)
class Route:
    adapter_name: str
    model: str

    def __str__(self) -> str:
        return f"{self.adapter_name}:{self.model}"


def parse_routes(text: str) -> tuple[Route, ...]:
    routes: list[Route] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        adapter, sep, model = item.partition(":")
        if not sep or not adapter.strip() or not model.strip():
            raise ValueError(f"route must be adapter:model, got {item!r}")
        routes.append(Route(adapter.strip(), model.strip()))
    return tuple(routes)


def routes_from_env(
    purpose: str, default: Sequence[Route], environ: Mapping[str, str] | None = None
) -> tuple[Route, ...]:
    env = os.environ if environ is None else environ
    text = env.get(ROUTES_ENV_PREFIX + purpose.upper(), "")
    return parse_routes(text) if text.strip() else tuple(default)


def next_utc_midnight(now: datetime) -> datetime:
    day = now.astimezone(UTC).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class RouteHealth:
    clock: Callable[[], datetime] = _now
    quota_reset: Callable[[datetime], datetime] = next_utc_midnight
    breaker_threshold: int = 3
    breaker_open: timedelta = timedelta(minutes=5)
    _blocked: dict[Route, tuple[datetime, LlmErrorKind]] = field(default_factory=dict)
    _failures: dict[Route, int] = field(default_factory=dict)

    def blocked(self, route: Route) -> LlmErrorKind | None:
        entry = self._blocked.get(route)
        if entry is None:
            return None
        until, kind = entry
        if self.clock() >= until:
            del self._blocked[route]
            self._failures.pop(route, None)
            return None
        return kind

    def record_success(self, route: Route) -> None:
        self._failures.pop(route, None)

    def record_failure(self, route: Route, error: LlmError) -> None:
        now = self.clock()
        if error.kind is LlmErrorKind.QUOTA_EXHAUSTED:
            self._blocked[route] = (self.quota_reset(now), error.kind)
            return
        server_side = error.kind is LlmErrorKind.UNAVAILABLE or (
            error.kind is LlmErrorKind.HTTP
            and error.status_code is not None
            and error.status_code >= 500
        )
        if not server_side:
            return
        count = self._failures.get(route, 0) + 1
        self._failures[route] = count
        if count >= self.breaker_threshold:
            self._blocked[route] = (now + self.breaker_open, LlmErrorKind.UNAVAILABLE)


@dataclass(frozen=True)
class RequestDefaults:
    max_tokens: int = 4096
    reasoning: ReasoningEffort = ReasoningEffort.LOW
    timeout_seconds: float | None = None


@dataclass
class Llm:
    """What domain code receives: ports by adapter name, routes, and health."""

    adapters: Mapping[str, LlmPort]
    routes: Mapping[str, tuple[Route, ...]]
    health: RouteHealth = field(default_factory=RouteHealth)
    defaults: RequestDefaults = field(default_factory=RequestDefaults)

    def routes_for(self, purpose: str) -> tuple[Route, ...]:
        return self.routes[purpose]

    def port(self, route: Route) -> LlmPort:
        try:
            return self.adapters[route.adapter_name]
        except KeyError:
            raise ValueError(f"unknown LLM adapter {route.adapter_name!r}") from None

    def request(
        self, route: Route, messages: Sequence[dict[str, str]]
    ) -> CompletionRequest:
        return CompletionRequest(
            model=route.model,
            messages=messages,
            max_tokens=self.defaults.max_tokens,
            reasoning=self.defaults.reasoning,
            timeout_seconds=self.defaults.timeout_seconds,
        )

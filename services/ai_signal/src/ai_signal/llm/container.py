"""Composition root for the LLM layer: adapters, routes, and health."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_signal.llm.adapters import ADAPTERS
from ai_signal.llm.port import LlmPort, ReasoningEffort
from ai_signal.llm.routing import (
    Llm,
    RequestDefaults,
    Route,
    RouteHealth,
    routes_from_env,
)

PURPOSES = ("document_judgement", "signal_generation")


class LlmSettings(BaseSettings):
    """Defaults reproduce the single-provider setup: OpenRouter primary then secondary."""

    model_config = SettingsConfigDict(
        env_prefix="AI_SIGNAL_LLM_", env_file=None, extra="ignore"
    )

    primary_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    secondary_model: str = "nvidia/nemotron-3.5-super-vl:free"
    max_tokens: int = 4096
    reasoning_effort: ReasoningEffort = ReasoningEffort.LOW
    timeout_seconds: float | None = None
    breaker_threshold: int = 3
    breaker_open_seconds: float = 300.0

    def default_routes(self) -> tuple[Route, ...]:
        return (
            Route("openrouter", self.primary_model),
            Route("openrouter", self.secondary_model),
        )


def build_llm(
    settings: LlmSettings | None = None,
    *,
    factories: Mapping[str, Callable[[], LlmPort]] = ADAPTERS,
    environ: Mapping[str, str] | None = None,
) -> Llm:
    settings = settings or LlmSettings()
    routes = {
        purpose: routes_from_env(purpose, settings.default_routes(), environ)
        for purpose in PURPOSES
    }
    used = {route.adapter_name for rs in routes.values() for route in rs}
    unknown = used - factories.keys()
    if unknown:
        raise ValueError(f"unknown LLM adapters in routes: {sorted(unknown)}")
    return Llm(
        adapters={name: factories[name]() for name in sorted(used)},
        routes=routes,
        health=RouteHealth(
            breaker_threshold=settings.breaker_threshold,
            breaker_open=timedelta(seconds=settings.breaker_open_seconds),
        ),
        defaults=RequestDefaults(
            max_tokens=settings.max_tokens,
            reasoning=settings.reasoning_effort,
            timeout_seconds=settings.timeout_seconds,
        ),
    )


@lru_cache(maxsize=1)
def shared_llm() -> Llm:
    """Process-wide container so each adapter keeps one concurrency limit."""
    return build_llm()

from __future__ import annotations

import asyncio

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from ai_signal.llm import (
    Llm,
    LlmError,
    LlmErrorKind,
    ModelStep,
    Route,
    RouteHealth,
    run_fallback,
)
from ai_signal.llm.adapters.fake import FakeAdapter
from ai_signal.llm.container import LlmSettings, build_llm
from ai_signal.llm.routing import next_utc_midnight, parse_routes, routes_from_env

T0 = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)


class Out(BaseModel):
    value: int


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def test_routes_split_on_the_first_colon_only() -> None:
    assert parse_routes("gemini:gemini-2.5-flash, openrouter:nvidia/x:free") == (
        Route("gemini", "gemini-2.5-flash"),
        Route("openrouter", "nvidia/x:free"),
    )
    with pytest.raises(ValueError):
        parse_routes("openrouter")


def test_routes_env_overrides_the_default_per_purpose() -> None:
    default = LlmSettings().default_routes()
    env = {"AI_SIGNAL_LLM_ROUTES_SIGNAL_GENERATION": "gemini:g"}
    assert routes_from_env("signal_generation", default, env) == (Route("gemini", "g"),)
    assert routes_from_env("document_judgement", default, env) == default
    assert default == (
        Route("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
        Route("openrouter", "nvidia/nemotron-3.5-super-vl:free"),
    )


def test_build_llm_creates_only_routed_adapters() -> None:
    made: list[str] = []

    def factory(name: str) -> FakeAdapter:
        made.append(name)
        return FakeAdapter()

    env = {"AI_SIGNAL_LLM_ROUTES_DOCUMENT_JUDGEMENT": "gemini:g,openrouter:o:free"}
    llm = build_llm(
        LlmSettings(),
        factories={"gemini": lambda: factory("gemini"), "openrouter": lambda: factory("openrouter")},
        environ=env,
    )
    assert sorted(made) == ["gemini", "openrouter"]
    assert llm.routes_for("document_judgement")[1] == Route("openrouter", "o:free")
    with pytest.raises(ValueError):
        build_llm(LlmSettings(), factories={}, environ=env)


def test_quota_blocks_until_next_utc_midnight() -> None:
    clock = Clock()
    health = RouteHealth(clock=clock)
    route = Route("gemini", "g")
    health.record_failure(route, LlmError(LlmErrorKind.QUOTA_EXHAUSTED, "day"))
    assert health.blocked(route) is LlmErrorKind.QUOTA_EXHAUSTED
    clock.now = next_utc_midnight(T0) - timedelta(seconds=1)
    assert health.blocked(route) is LlmErrorKind.QUOTA_EXHAUSTED
    clock.now = next_utc_midnight(T0)
    assert health.blocked(route) is None


def test_breaker_opens_after_repeated_server_errors_and_closes() -> None:
    clock = Clock()
    health = RouteHealth(clock=clock, breaker_threshold=3, breaker_open=timedelta(minutes=5))
    route = Route("openrouter", "o")
    health.record_failure(route, LlmError(LlmErrorKind.UNAVAILABLE, "x"))
    health.record_failure(route, LlmError(LlmErrorKind.HTTP, "x", status_code=502))
    health.record_failure(route, LlmError(LlmErrorKind.RATE_LIMIT, "x"))
    assert health.blocked(route) is None
    health.record_failure(route, LlmError(LlmErrorKind.UNAVAILABLE, "x"))
    assert health.blocked(route) is LlmErrorKind.UNAVAILABLE
    clock.now = T0 + timedelta(minutes=5)
    assert health.blocked(route) is None


def test_success_resets_the_breaker_count() -> None:
    health = RouteHealth(breaker_threshold=2)
    route = Route("openrouter", "o")
    health.record_failure(route, LlmError(LlmErrorKind.UNAVAILABLE, "x"))
    health.record_success(route)
    health.record_failure(route, LlmError(LlmErrorKind.UNAVAILABLE, "x"))
    assert health.blocked(route) is None


def test_gemini_quota_falls_through_to_openrouter_and_is_skipped_next_time() -> None:
    asyncio.run(_gemini_quota_falls_through_to_openrouter_and_is_skipped_next_time())


async def _gemini_quota_falls_through_to_openrouter_and_is_skipped_next_time() -> None:
    gemini = FakeAdapter([LlmError(LlmErrorKind.QUOTA_EXHAUSTED, "day")])
    openrouter = FakeAdapter(['{"value": 1}', '{"value": 2}'])
    routes = (Route("gemini", "g"), Route("openrouter", "o:free"))
    llm = Llm(
        adapters={"gemini": gemini, "openrouter": openrouter},
        routes={"p": routes},
        health=RouteHealth(clock=Clock()),
    )
    steps = [
        ModelStep(name=n, llm=llm, route=r, output_type=Out, messages=lambda _: [])
        for n, r in zip(("primary", "secondary"), routes, strict=True)
    ]
    first = await run_fallback(steps, None)
    second = await run_fallback(steps, None)
    assert first.result is not None and first.result.value.value == 1
    assert second.result is not None and second.result.value.value == 2
    assert (gemini.calls, openrouter.calls) == (1, 2)
    skipped = second.failures.attempts[0].as_json()
    assert skipped == {
        "step": "primary",
        "adapter": "gemini",
        "model": "g",
        "error": "QUOTA_EXHAUSTED",
        "raw_content": None,
    }
    assert second.failures.attempts[1].adapter == "openrouter"

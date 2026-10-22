from __future__ import annotations

import asyncio
import importlib
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_signal.llm import LlmError, LlmErrorKind, ModelStep, ReasoningEffort, Route
from ai_signal.llm.adapters.fake import FakeAdapter
from ai_signal.llm.cache import (
    CacheEntry,
    CachingPort,
    InMemoryCacheStore,
    PostgresCacheStore,
    cache_key,
)
from ai_signal.llm.container import LlmSettings, build_llm
from ai_signal.llm.fallback import StepFailures
from ai_signal.llm.port import CompletionRequest
from alembic import command

PostgresContainer = importlib.import_module(
    "testcontainers.community.postgres"
).PostgresContainer

SERVICE_ROOT = Path(__file__).resolve().parents[1]


class Answer(BaseModel):
    value: int


class OtherAnswer(BaseModel):
    value: int
    note: str = ""


REQUEST = CompletionRequest(
    model="m", messages=[{"role": "user", "content": "q"}], max_tokens=100
)


def _cached(fake: FakeAdapter, store: InMemoryCacheStore) -> CachingPort:
    return CachingPort(inner=fake, store=store, adapter_name="fake")


def test_key_is_stable_and_sensitive() -> None:
    base = cache_key("fake", REQUEST, Answer)
    same = CompletionRequest(
        model="m",
        messages=({"content": "q", "role": "user"},),
        max_tokens=100,
        timeout_seconds=9.0,
    )
    assert cache_key("fake", same, Answer) == base
    variants = [
        cache_key("other", REQUEST, Answer),
        cache_key("fake", replace(REQUEST, model="n"), Answer),
        cache_key(
            "fake", replace(REQUEST, messages=[{"role": "user", "content": "r"}]), Answer
        ),
        cache_key("fake", replace(REQUEST, max_tokens=101), Answer),
        cache_key("fake", replace(REQUEST, reasoning=ReasoningEffort.HIGH), Answer),
        cache_key("fake", REQUEST, OtherAnswer),
    ]
    assert base not in variants
    assert len(set(variants)) == len(variants)


def test_miss_stores_and_hit_skips_inner() -> None:
    fake = FakeAdapter(replies=['{"value": 1}'])
    store = InMemoryCacheStore()
    port = _cached(fake, store)
    first = asyncio.run(port.complete(REQUEST, Answer))
    second = asyncio.run(port.complete(REQUEST, Answer))
    assert fake.calls == 1
    assert not first.cached
    assert second.cached
    assert second.output == Answer(value=1)
    assert second.model_version == "m"
    assert len(store.entries) == 1


def test_errors_are_not_cached() -> None:
    fake = FakeAdapter(
        replies=[LlmError(LlmErrorKind.TIMEOUT, "slow"), "not json", '{"value": 2}']
    )
    store = InMemoryCacheStore()
    port = _cached(fake, store)
    for _ in range(2):
        with pytest.raises(LlmError):
            asyncio.run(port.complete(REQUEST, Answer))
    assert store.entries == {}
    assert asyncio.run(port.complete(REQUEST, Answer)).output == Answer(value=2)
    assert fake.calls == 3


def test_invalid_cached_content_falls_back_to_inner_and_overwrites() -> None:
    key = cache_key("fake", REQUEST, Answer)
    store = InMemoryCacheStore(
        entries={key: CacheEntry("fake", "m", '{"value": "x"}', "m")}
    )
    fake = FakeAdapter(replies=['{"value": 3}'])
    completion = asyncio.run(_cached(fake, store).complete(REQUEST, Answer))
    assert not completion.cached
    assert fake.calls == 1
    assert store.entries[key].raw_content == '{"value": 3}'


def test_build_llm_wraps_only_when_enabled() -> None:
    env = {"AI_SIGNAL_LLM_ROUTES_DOCUMENT_JUDGEMENT": "fake:m"}
    factories = {"fake": FakeAdapter, "openrouter": FakeAdapter}
    plain = build_llm(LlmSettings(), factories=factories, environ=env)
    assert all(isinstance(p, FakeAdapter) for p in plain.adapters.values())
    store = InMemoryCacheStore()
    wrapped = build_llm(
        LlmSettings(cache="postgres"),
        factories=factories,
        environ=env,
        cache_store=store,
    )
    for name, port in wrapped.adapters.items():
        assert isinstance(port, CachingPort)
        assert port.adapter_name == name
        assert port.store is store


def test_cache_setting_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_SIGNAL_LLM_CACHE", raising=False)
    assert LlmSettings().cache == "off"
    monkeypatch.setenv("AI_SIGNAL_LLM_CACHE", "postgres")
    assert LlmSettings().cache == "postgres"


def test_attempts_show_cached() -> None:
    fake = FakeAdapter(replies=['{"value": 4}'])
    store = InMemoryCacheStore()
    llm = build_llm(
        LlmSettings(cache="postgres"),
        factories={"fake": lambda: fake, "openrouter": FakeAdapter},
        environ={"AI_SIGNAL_LLM_ROUTES_DOCUMENT_JUDGEMENT": "fake:m"},
        cache_store=store,
    )
    step = ModelStep(
        name="primary",
        llm=llm,
        route=Route("fake", "m"),
        output_type=Answer,
        messages=lambda _: [{"role": "user", "content": "q"}],
    )
    flags = []
    for _ in range(2):
        failures = StepFailures()
        result = asyncio.run(step.attempt(None, failures))
        assert result is not None and result.value == Answer(value=4)
        flags.append(failures.attempts[-1].as_json()["cached"])
    assert flags == [False, True]
    assert fake.calls == 1


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with (
        pytest.MonkeyPatch.context() as patch,
        PostgresContainer("postgres:16-alpine") as container,
    ):
        url = (
            container.get_connection_url()
            .replace("postgresql+psycopg2", "postgresql+asyncpg")
            .replace("postgresql://", "postgresql+asyncpg://")
        )
        patch.setenv("AI_SIGNAL_DATABASE_URL", url)
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
        command.upgrade(config, "head")
        yield url


def test_postgres_store_round_trip(database_url: str) -> None:
    async def run() -> tuple[CacheEntry | None, CacheEntry | None, int, int]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        store = PostgresCacheStore(engine)
        missing = await store.get("k")
        await store.put("k", CacheEntry("fake", "m", '{"value": 1}', "v1"))
        _ = await store.get("k")
        await store.put("k", CacheEntry("fake", "m", '{"value": 2}', "v2"))
        found = await store.get("k")
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text("SELECT hit_count, count(*) OVER () AS n FROM gold.llm_cache")
                )
            ).one()
        await engine.dispose()
        return missing, found, row.hit_count, row.n

    missing, found, hits, rows = asyncio.run(run())
    assert missing is None
    assert found == CacheEntry("fake", "m", '{"value": 2}', "v2")
    assert hits == 1
    assert rows == 1

from __future__ import annotations

import asyncio

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from ai_signal.llm import CompletionRequest, LlmError, LlmErrorKind, ReasoningEffort
from ai_signal.llm.adapters.gemini import GeminiAdapter, GeminiSettings
from ai_signal.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from ai_signal.llm.adapters.openrouter import OpenRouterAdapter, OpenRouterSettings

Handler = Callable[[httpx.Request], httpx.Response]


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


def openrouter(handler: Handler) -> OpenAICompatibleAdapter:
    settings = OpenRouterSettings.model_validate({"api_key": "k"})
    http = httpx.AsyncClient(base_url=settings.base_url, transport=httpx.MockTransport(handler))
    return OpenRouterAdapter(settings, http=http)


def gemini(handler: Handler) -> OpenAICompatibleAdapter:
    settings = GeminiSettings.model_validate({"api_key": "k"})
    http = httpx.AsyncClient(base_url=settings.base_url, transport=httpx.MockTransport(handler))
    return GeminiAdapter(settings, http=http)


ADAPTERS = pytest.mark.parametrize("make", [openrouter, gemini], ids=["openrouter", "gemini"])
REQUEST = CompletionRequest(model="m", messages=[{"role": "user", "content": "hi"}])


def reply(content: str) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "model": "m-v1"})

    return handle


def status(code: int, body: object = None) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json=body if body is not None else {"error": "x"})

    return handle


@ADAPTERS
def test_valid_answer_is_parsed(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    asyncio.run(_valid_answer_is_parsed(make))


async def _valid_answer_is_parsed(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    completion = await make(reply('{"value": 3}')).complete(REQUEST, Out)
    assert (completion.output.value, completion.model_version) == (3, "m-v1")


@ADAPTERS
def test_json_fence_is_stripped(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    asyncio.run(_json_fence_is_stripped(make))


async def _json_fence_is_stripped(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    completion = await make(reply('```json\n{"value": 4}\n```')).complete(REQUEST, Out)
    assert completion.output.value == 4


@ADAPTERS
def test_schema_failure_keeps_raw_content(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    asyncio.run(_schema_failure_keeps_raw_content(make))


async def _schema_failure_keeps_raw_content(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    with pytest.raises(LlmError) as info:
        await make(reply("not json")).complete(REQUEST, Out)
    assert (info.value.kind, info.value.raw_content) == (LlmErrorKind.SCHEMA, "not json")


@ADAPTERS
def test_empty_body(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    asyncio.run(_empty_body(make))


async def _empty_body(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    with pytest.raises(LlmError) as info:
        await make(reply("  ")).complete(REQUEST, Out)
    assert info.value.kind is LlmErrorKind.EMPTY


@ADAPTERS
@pytest.mark.parametrize(
    ("code", "kind"),
    [(429, LlmErrorKind.RATE_LIMIT), (503, LlmErrorKind.UNAVAILABLE), (500, LlmErrorKind.HTTP)],
)
def test_status_mapping(
    make: Callable[[Handler], OpenAICompatibleAdapter], code: int, kind: LlmErrorKind
) -> None:
    asyncio.run(_status_mapping(make, code, kind))


async def _status_mapping(
    make: Callable[[Handler], OpenAICompatibleAdapter], code: int, kind: LlmErrorKind
) -> None:
    with pytest.raises(LlmError) as info:
        await make(status(code)).complete(REQUEST, Out)
    assert (info.value.kind, info.value.status_code) == (kind, code)


@ADAPTERS
def test_timeout(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    asyncio.run(_timeout(make))


async def _timeout(make: Callable[[Handler], OpenAICompatibleAdapter]) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LlmError) as info:
        await make(handle).complete(REQUEST, Out)
    assert info.value.kind is LlmErrorKind.TIMEOUT


def capture(seen: list[dict[str, object]]) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return reply('{"value": 1}')(request)

    return handle


def test_openrouter_payload_has_reasoning_object_and_strict_schema() -> None:
    asyncio.run(_openrouter_payload_has_reasoning_object_and_strict_schema())


async def _openrouter_payload_has_reasoning_object_and_strict_schema() -> None:
    seen: list[dict[str, object]] = []
    await openrouter(capture(seen)).complete(REQUEST, Out)
    body = seen[0]
    assert body["reasoning"] == {"max_tokens": 2048, "exclude": True}
    assert "reasoning_effort" not in body
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Out", "strict": True, "schema": Out.model_json_schema()},
    }


def test_gemini_payload_has_effort_and_no_reasoning_object() -> None:
    asyncio.run(_gemini_payload_has_effort_and_no_reasoning_object())


async def _gemini_payload_has_effort_and_no_reasoning_object() -> None:
    seen: list[dict[str, object]] = []
    request = CompletionRequest(model="g", messages=[], reasoning=ReasoningEffort.MEDIUM)
    await gemini(capture(seen)).complete(request, Out)
    body = seen[0]
    assert body["reasoning_effort"] == "medium"
    assert "reasoning" not in body
    assert '"strict": true' in json.dumps(body["response_format"])


def test_gemini_daily_quota_is_exhausted_not_rate_limited() -> None:
    asyncio.run(_gemini_daily_quota_is_exhausted_not_rate_limited())


async def _gemini_daily_quota_is_exhausted_not_rate_limited() -> None:
    body = {"error": {"code": 429, "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}
    with pytest.raises(LlmError) as info:
        await gemini(status(429, body)).complete(REQUEST, Out)
    assert info.value.kind is LlmErrorKind.QUOTA_EXHAUSTED


def test_gemini_per_minute_429_is_rate_limit() -> None:
    asyncio.run(_gemini_per_minute_429_is_rate_limit())


async def _gemini_per_minute_429_is_rate_limit() -> None:
    body = {"error": {"code": 429, "details": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}}
    with pytest.raises(LlmError) as info:
        await gemini(status(429, body)).complete(REQUEST, Out)
    assert info.value.kind is LlmErrorKind.RATE_LIMIT


def test_openrouter_key_keeps_the_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "legacy")
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert OpenRouterSettings().api_key == "legacy"
    assert GeminiSettings().api_key == "g"

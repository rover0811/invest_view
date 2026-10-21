from __future__ import annotations

import asyncio
import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from ai_signal.llm import (
    LlmAttempt,
    LlmError,
    LlmErrorKind,
    LlmSettings,
    ModelSpec,
    ModelStep,
    OpenRouterClient,
    StepFailures,
    StepResult,
    primary_spec,
    run_fallback,
    secondary_spec,
)
from ai_signal.llm.client import build_payload


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    sign: Literal[-1, 0, 1]


def _settings(**overrides: object) -> LlmSettings:
    values: dict[str, object] = {"api_key": "test-key", "max_concurrency": 2}
    values.update(overrides)
    return LlmSettings.model_validate(values)


def _body(content: str | None, model: str = "vendor/model-2026") -> dict[str, object]:
    return {"model": model, "choices": [{"message": {"content": content}}]}


def _client(handler: httpx.MockTransport, **overrides: object) -> OpenRouterClient:
    settings = _settings(**overrides)
    http = httpx.AsyncClient(base_url=settings.base_url, transport=handler)
    return OpenRouterClient(settings, http=http)


MESSAGES = [{"role": "user", "content": "hi"}]


def test_settings_read_openrouter_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    settings = LlmSettings()
    assert settings.api_key == "from-env"
    assert "from-env" not in repr(settings)
    assert settings.primary_model == "nvidia/nemotron-3-super-120b-a12b:free"
    assert settings.secondary_model == "nvidia/nemotron-3.5-super-vl:free"


def test_primary_request_uses_json_schema_and_separate_reasoning_limit() -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_body('{"label": "up", "sign": 1}'))

    client = _client(httpx.MockTransport(handle))
    spec = primary_spec(client.settings)
    completion = asyncio.run(client.complete(spec, MESSAGES, Answer))

    assert completion.output == Answer(label="up", sign=1)
    assert completion.model_version == "vendor/model-2026"
    (payload,) = seen
    assert payload["model"] == "nvidia/nemotron-3-super-120b-a12b:free"
    assert payload["temperature"] == 0.0
    assert payload["reasoning"] == {"max_tokens": 2048, "exclude": True}
    assert payload["max_tokens"] == 4096
    response_format = payload["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"] == Answer.model_json_schema()


def test_secondary_request_uses_json_schema_with_reasoning_limit() -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_body('{"label": "down", "sign": -1}'))

    client = _client(httpx.MockTransport(handle))
    spec = secondary_spec(client.settings)
    completion = asyncio.run(client.complete(spec, MESSAGES, Answer))

    assert completion.output.sign == -1
    assert spec.model == "nvidia/nemotron-3.5-super-vl:free"
    response_format = seen[0]["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert seen[0]["reasoning"] == {"max_tokens": 2048, "exclude": True}


def test_openrouter_payload_has_no_reasoning_effort() -> None:
    spec = primary_spec(_settings())
    payload = build_payload(spec, MESSAGES, Answer)
    assert spec.provider == "openrouter"
    assert payload["reasoning"] == {"max_tokens": 2048, "exclude": True}
    assert "reasoning_effort" not in payload


def test_gemini_payload_drops_reasoning_object(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SIGNAL_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("AI_SIGNAL_LLM_PRIMARY_MODEL", "gemini-x-flash")
    settings = LlmSettings()
    for spec in (primary_spec(settings), secondary_spec(settings)):
        payload = build_payload(spec, MESSAGES, Answer)
        assert spec.provider == "gemini"
        assert "reasoning" not in payload
        assert payload["reasoning_effort"] == "low"
        response_format = payload["response_format"]
        assert isinstance(response_format, dict)
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
    assert primary_spec(settings).model == "gemini-x-flash"


def test_json_object_mode_revalidates_fenced_body() -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json=_body('```json\n{"label": "down", "sign": -1}\n```')
        )

    client = _client(httpx.MockTransport(handle))
    spec = ModelSpec(
        model="json-object-model", response_mode="json_object", max_tokens=64
    )
    completion = asyncio.run(client.complete(spec, MESSAGES, Answer))

    assert completion.output.sign == -1
    assert completion.raw_content == '```json\n{"label": "down", "sign": -1}\n```'
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "reasoning" not in seen[0]


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (httpx.Response(200, json=_body("")), LlmErrorKind.EMPTY),
        (httpx.Response(200, json=_body(None)), LlmErrorKind.EMPTY),
        (
            httpx.Response(200, json=_body('{"label": "x", "sign": 5}')),
            LlmErrorKind.SCHEMA,
        ),
        (httpx.Response(200, json=_body("not json")), LlmErrorKind.SCHEMA),
        (httpx.Response(500, json={}), LlmErrorKind.HTTP),
        (httpx.Response(200, json={"error": {"code": 502}}), LlmErrorKind.HTTP),
    ],
)
def test_failures_map_to_error_kinds(
    response: httpx.Response, kind: LlmErrorKind
) -> None:
    client = _client(httpx.MockTransport(lambda _request: response))
    with pytest.raises(LlmError) as raised:
        _ = asyncio.run(
            client.complete(primary_spec(client.settings), MESSAGES, Answer)
        )
    assert raised.value.kind is kind


@pytest.mark.parametrize(
    ("content", "kind", "raw"),
    [
        ('{"label": "x", "sign": 5}', LlmErrorKind.SCHEMA, '{"label": "x", "sign": 5}'),
        ("not json", LlmErrorKind.SCHEMA, "not json"),
        ("  ", LlmErrorKind.EMPTY, "  "),
        (None, LlmErrorKind.EMPTY, None),
    ],
)
def test_validation_failures_keep_raw_content(
    content: str | None, kind: LlmErrorKind, raw: str | None
) -> None:
    client = _client(
        httpx.MockTransport(lambda _r: httpx.Response(200, json=_body(content)))
    )
    with pytest.raises(LlmError) as raised:
        _ = asyncio.run(
            client.complete(primary_spec(client.settings), MESSAGES, Answer)
        )
    assert raised.value.kind is kind
    assert raised.value.raw_content == raw


def test_transport_failures_have_no_raw_content() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(500, json={})))
    with pytest.raises(LlmError) as raised:
        _ = asyncio.run(
            client.complete(primary_spec(client.settings), MESSAGES, Answer)
        )
    assert raised.value.raw_content is None


def test_rate_limit_keeps_retry_after() -> None:
    client = _client(
        httpx.MockTransport(
            lambda _request: httpx.Response(429, headers={"Retry-After": "7"}, json={})
        )
    )
    with pytest.raises(LlmError) as raised:
        _ = asyncio.run(
            client.complete(primary_spec(client.settings), MESSAGES, Answer)
        )
    assert raised.value.kind is LlmErrorKind.RATE_LIMIT
    assert raised.value.retry_after == 7.0


def test_timeout_maps_to_timeout() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = _client(httpx.MockTransport(handle))
    with pytest.raises(LlmError) as raised:
        _ = asyncio.run(
            client.complete(primary_spec(client.settings), MESSAGES, Answer)
        )
    assert raised.value.kind is LlmErrorKind.TIMEOUT


def test_global_concurrency_limit() -> None:
    state = {"active": 0, "peak": 0}

    async def handle(_request: httpx.Request) -> httpx.Response:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return httpx.Response(200, json=_body('{"label": "ok", "sign": 0}'))

    async def scenario() -> None:
        client = _client(httpx.MockTransport(handle), max_concurrency=2)
        spec = secondary_spec(client.settings)
        _ = await asyncio.gather(
            *(client.complete(spec, MESSAGES, Answer) for _ in range(6))
        )

    asyncio.run(scenario())
    assert state["peak"] == 2


def _step(
    name: str, client: OpenRouterClient, spec: ModelSpec
) -> ModelStep[str, Answer]:
    return ModelStep(
        name=name,
        client=client,
        spec=spec,
        output_type=Answer,
        messages=lambda request, _spec: [{"role": "user", "content": request}],
    )


def test_fallback_passes_failed_step_to_next() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if model == "nvidia/nemotron-3-super-120b-a12b:free":
            return httpx.Response(200, json=_body(""))
        return httpx.Response(200, json=_body('{"label": "b", "sign": 1}', model))

    client = _client(httpx.MockTransport(handle))
    steps = (
        _step("primary", client, primary_spec(client.settings)),
        _step("secondary", client, secondary_spec(client.settings)),
    )
    outcome = asyncio.run(run_fallback(steps, "title"))

    assert outcome.result is not None
    assert outcome.result.value.label == "b"
    assert outcome.result.decided_by == "llm"
    assert outcome.result.model_version == "nvidia/nemotron-3.5-super-vl:free"
    assert outcome.failures.errors == [("primary", LlmErrorKind.EMPTY)]
    assert outcome.failures.attempts == [
        LlmAttempt(
            "primary", "nvidia/nemotron-3-super-120b-a12b:free", LlmErrorKind.EMPTY, ""
        ),
        LlmAttempt(
            "secondary",
            "nvidia/nemotron-3.5-super-vl:free",
            None,
            '{"label": "b", "sign": 1}',
        ),
    ]
    assert outcome.failures.attempts[0].as_json() == {
        "step": "primary",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "error": "EMPTY",
        "raw_content": "",
    }


def test_fallback_returns_none_when_every_step_passes() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(429, json={})))
    steps = (
        _step("primary", client, primary_spec(client.settings)),
        _step("secondary", client, secondary_spec(client.settings)),
    )
    outcome = asyncio.run(run_fallback(steps, "title"))
    assert outcome.result is None
    assert outcome.failures.last_error is LlmErrorKind.RATE_LIMIT
    assert len(outcome.failures.errors) == 2


class RuleStep:
    name: str = "rule"

    async def attempt(
        self, request: str, failures: StepFailures
    ) -> StepResult[Answer] | None:
        _ = failures
        return StepResult(value=Answer(label=request, sign=0), decided_by="flow_rule")


def test_non_model_steps_plug_into_the_same_list() -> None:
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(500, json={})))
    steps = (_step("primary", client, primary_spec(client.settings)), RuleStep())
    outcome = asyncio.run(run_fallback(steps, "rule text"))
    assert outcome.result is not None
    assert outcome.result.decided_by == "flow_rule"
    assert outcome.result.model_version is None

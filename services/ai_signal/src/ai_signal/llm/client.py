"""OpenRouter chat completions client shared by the document processor and the signal worker.

One ``OpenRouterClient`` per process owns the global concurrency limit (12.5,
18.10 "LLM 호출 수"), so every caller must reuse ``shared_client()``. A call
returns a pydantic model validated from the response body; every failure is an
``LlmError`` with a ``kind`` that maps to ``signal_retry.last_error``.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Generic, Literal, TypeVar

import httpx
from pydantic import AliasChoices, BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ResponseMode = Literal["json_schema", "json_object"]
Provider = Literal["openrouter", "gemini"]
ReasoningEffort = Literal["low", "medium", "high"]
T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class LlmErrorKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    SCHEMA = "SCHEMA"
    EMPTY = "EMPTY"
    HTTP = "HTTP"


class LlmError(RuntimeError):
    """``raw_content`` is the assistant text when a body arrived but failed validation."""

    def __init__(
        self,
        kind: LlmErrorKind,
        message: str,
        *,
        retry_after: float | None = None,
        raw_content: str | None = None,
    ) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind: LlmErrorKind = kind
        self.retry_after: float | None = retry_after
        self.raw_content: str | None = raw_content


class LlmSettings(BaseSettings):
    """LLM configuration. The API key is read from ``OPENROUTER_API_KEY``.

    ``provider`` picks the request dialect: ``gemini`` targets Gemini's
    OpenAI-compatible endpoint, which rejects OpenRouter's ``reasoning`` object.
    """

    model_config = SettingsConfigDict(
        env_prefix="AI_SIGNAL_LLM_",
        env_file=None,
        extra="ignore",
        populate_by_name=True,
    )

    api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "AI_SIGNAL_LLM_API_KEY"),
    )
    provider: Provider = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    primary_model: str = "nvidia/nemotron-3-super-120b-a12b:free"
    secondary_model: str = "nvidia/nemotron-3.5-super-vl:free"
    max_tokens: int = 4096
    reasoning_max_tokens: int = 2048
    gemini_reasoning_effort: ReasoningEffort = "low"
    max_concurrency: int = 2
    timeout_seconds: float = 90.0


@dataclass(frozen=True)
class ModelSpec:
    """One model endpoint. ``reasoning_max_tokens`` caps thinking separately (18.8).

    OpenRouter takes the cap as a ``reasoning`` object; Gemini takes only
    ``reasoning_effort``.
    """

    model: str
    response_mode: ResponseMode
    max_tokens: int
    reasoning_max_tokens: int | None = None
    temperature: float = 0.0
    provider: Provider = "openrouter"
    reasoning_effort: ReasoningEffort | None = None


def _spec(settings: LlmSettings, model: str) -> ModelSpec:
    if settings.provider == "gemini":
        return ModelSpec(
            model=model,
            response_mode="json_schema",
            max_tokens=settings.max_tokens,
            provider="gemini",
            reasoning_effort=settings.gemini_reasoning_effort,
        )
    return ModelSpec(
        model=model,
        response_mode="json_schema",
        max_tokens=settings.max_tokens,
        reasoning_max_tokens=settings.reasoning_max_tokens,
    )


def primary_spec(settings: LlmSettings) -> ModelSpec:
    return _spec(settings, settings.primary_model)


def secondary_spec(settings: LlmSettings) -> ModelSpec:
    return _spec(settings, settings.secondary_model)


@dataclass(frozen=True)
class Completion(Generic[T]):
    output: T
    model_version: str
    raw_content: str


def json_schema_text(output_type: type[BaseModel]) -> str:
    return json.dumps(output_type.model_json_schema(), ensure_ascii=False)


def build_payload(
    spec: ModelSpec, messages: list[dict[str, str]], output_type: type[BaseModel]
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": spec.model,
        "messages": messages,
        "temperature": spec.temperature,
        "max_tokens": spec.max_tokens,
    }
    if spec.response_mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": output_type.__name__,
                "strict": True,
                "schema": output_type.model_json_schema(),
            },
        }
    else:
        payload["response_format"] = {"type": "json_object"}
    if spec.reasoning_effort is not None:
        payload["reasoning_effort"] = spec.reasoning_effort
    if spec.provider == "openrouter" and spec.reasoning_max_tokens is not None:
        payload["reasoning"] = {
            "max_tokens": spec.reasoning_max_tokens,
            "exclude": True,
        }
    return payload


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def parse_completion(
    body: object, spec: ModelSpec, output_type: type[T]
) -> Completion[T]:
    if not isinstance(body, dict):
        raise LlmError(LlmErrorKind.HTTP, "response is not a JSON object")
    if "error" in body:
        raise LlmError(LlmErrorKind.HTTP, f"provider error: {body['error']}")
    choices = body.get("choices")
    content: object = None
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LlmError(
            LlmErrorKind.EMPTY,
            f"{spec.model} returned an empty body",
            raw_content=content if isinstance(content, str) else None,
        )
    text = _FENCE.sub("", content.strip())
    try:
        output = output_type.model_validate_json(text)
    except ValidationError as exc:
        raise LlmError(LlmErrorKind.SCHEMA, str(exc), raw_content=content) from exc
    model = body.get("model")
    return Completion(
        output=output,
        model_version=model if isinstance(model, str) else spec.model,
        raw_content=content,
    )


class OpenRouterClient:
    def __init__(
        self, settings: LlmSettings, *, http: httpx.AsyncClient | None = None
    ) -> None:
        if settings.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._settings = settings
        self._http = http
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)

    @property
    def settings(self) -> LlmSettings:
        return self._settings

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._settings.base_url,
                timeout=self._settings.timeout_seconds,
            )
        return self._http

    async def complete(
        self,
        spec: ModelSpec,
        messages: list[dict[str, str]],
        output_type: type[T],
    ) -> Completion[T]:
        payload = build_payload(spec, messages, output_type)
        async with self._semaphore:
            try:
                response = await self._client().post(
                    "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._settings.api_key}"},
                )
            except httpx.TimeoutException as exc:
                raise LlmError(LlmErrorKind.TIMEOUT, f"{spec.model} timed out") from exc
            except httpx.HTTPError as exc:
                raise LlmError(LlmErrorKind.HTTP, f"{spec.model}: {exc}") from exc
        if response.status_code == 429:
            raise LlmError(
                LlmErrorKind.RATE_LIMIT,
                f"{spec.model} rate limited",
                retry_after=_retry_after(response),
            )
        if response.status_code >= 400:
            raise LlmError(
                LlmErrorKind.HTTP, f"{spec.model} status={response.status_code}"
            )
        try:
            body: object = response.json()
        except ValueError as exc:
            raise LlmError(LlmErrorKind.HTTP, "response is not JSON") from exc
        return parse_completion(body, spec, output_type)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()


@lru_cache(maxsize=1)
def shared_client() -> OpenRouterClient:
    """Process-wide client so all callers share one concurrency limit."""
    return OpenRouterClient(LlmSettings())

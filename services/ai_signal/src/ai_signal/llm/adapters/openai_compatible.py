"""Shared OpenAI-compatible chat completions call used by concrete adapters."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ai_signal.llm.port import (
    Completion,
    CompletionRequest,
    LlmError,
    LlmErrorKind,
)

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def json_schema_text(output_type: type[BaseModel]) -> str:
    return json.dumps(output_type.model_json_schema(), ensure_ascii=False)


def strict_schema_format(output_type: type[BaseModel]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": output_type.__name__,
            "strict": True,
            "schema": output_type.model_json_schema(),
        },
    }


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def parse_completion(
    body: object, model: str, output_type: type[T]
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
            f"{model} returned an empty body",
            raw_content=content if isinstance(content, str) else None,
        )
    text = _FENCE.sub("", content.strip())
    try:
        output = output_type.model_validate_json(text)
    except ValidationError as exc:
        raise LlmError(LlmErrorKind.SCHEMA, str(exc), raw_content=content) from exc
    version = body.get("model")
    return Completion(
        output=output,
        model_version=version if isinstance(version, str) else model,
        raw_content=content,
    )


class OpenAICompatibleAdapter:
    """Base adapter: subclasses add provider fields and error mapping."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        max_concurrency: int,
        timeout_seconds: float,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._http = http
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def provider_fields(self, request: CompletionRequest) -> dict[str, object]:
        return {}

    def build_payload(
        self, request: CompletionRequest, output_type: type[BaseModel]
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": request.model,
            "messages": list(request.messages),
            "temperature": 0.0,
            "max_tokens": request.max_tokens,
            "response_format": strict_schema_format(output_type),
        }
        payload.update(self.provider_fields(request))
        return payload

    def map_status(self, response: httpx.Response, model: str) -> LlmError:
        status = response.status_code
        if status == 429:
            return LlmError(
                LlmErrorKind.RATE_LIMIT,
                f"{model} rate limited",
                retry_after=_retry_after(response),
                status_code=status,
            )
        if status == 503:
            return LlmError(
                LlmErrorKind.UNAVAILABLE, f"{model} unavailable", status_code=status
            )
        return LlmError(
            LlmErrorKind.HTTP, f"{model} status={status}", status_code=status
        )

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            )
        return self._http

    async def complete(
        self, request: CompletionRequest, output_type: type[T]
    ) -> Completion[T]:
        payload = self.build_payload(request, output_type)
        timeout = request.timeout_seconds or self._timeout
        async with self._semaphore:
            try:
                response = await self._client().post(
                    "/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                raise LlmError(
                    LlmErrorKind.TIMEOUT, f"{request.model} timed out"
                ) from exc
            except httpx.HTTPError as exc:
                raise LlmError(LlmErrorKind.HTTP, f"{request.model}: {exc}") from exc
        if response.status_code >= 400:
            raise self.map_status(response, request.model)
        try:
            body: object = response.json()
        except ValueError as exc:
            raise LlmError(LlmErrorKind.HTTP, "response is not JSON") from exc
        return parse_completion(body, request.model, output_type)

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

"""Scripted adapter for tests: replies are JSON text, an HTTP status, or an ``LlmError``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ai_signal.llm.port import (
    Completion,
    CompletionRequest,
    LlmError,
    LlmErrorKind,
)

T = TypeVar("T", bound=BaseModel)
Reply = str | int | LlmError

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def status_error(status: int, model: str) -> LlmError:
    if status == 429:
        return LlmError(LlmErrorKind.RATE_LIMIT, f"{model} rate limited", status_code=429)
    if status == 503:
        return LlmError(LlmErrorKind.UNAVAILABLE, f"{model} unavailable", status_code=503)
    return LlmError(LlmErrorKind.HTTP, f"{model} status={status}", status_code=status)


@dataclass
class FakeAdapter:
    """An empty script answers 503, like a provider that went away."""

    replies: list[Reply] = field(default_factory=list)
    requests: list[dict[str, object]] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.requests)

    async def complete(
        self, request: CompletionRequest, output_type: type[T]
    ) -> Completion[T]:
        self.requests.append(
            {"model": request.model, "messages": list(request.messages)}
        )
        reply = self.replies.pop(0) if self.replies else 503
        if isinstance(reply, LlmError):
            raise reply
        if isinstance(reply, int):
            raise status_error(reply, request.model)
        if not reply.strip():
            raise LlmError(LlmErrorKind.EMPTY, "empty body", raw_content=reply)
        try:
            output = output_type.model_validate_json(_FENCE.sub("", reply.strip()))
        except ValidationError as exc:
            raise LlmError(LlmErrorKind.SCHEMA, str(exc), raw_content=reply) from exc
        return Completion(output=output, model_version=request.model, raw_content=reply)

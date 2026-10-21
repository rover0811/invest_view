"""Provider-neutral LLM port. Domain code depends on this module only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LlmErrorKind(StrEnum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UNAVAILABLE = "UNAVAILABLE"
    SCHEMA = "SCHEMA"
    EMPTY = "EMPTY"
    HTTP = "HTTP"


class LlmError(RuntimeError):
    """``raw_content`` is the assistant text when a body arrived but failed validation."""

    def __init__(
        self,
        kind: LlmErrorKind,
        message: str,
        raw_content: str | None = None,
        retry_after: float | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(f"{kind.value}: {message}")
        self.kind: LlmErrorKind = kind
        self.raw_content: str | None = raw_content
        self.retry_after: float | None = retry_after
        self.status_code: int | None = status_code


class ReasoningEffort(StrEnum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: Sequence[dict[str, str]]
    max_tokens: int = 4096
    reasoning: ReasoningEffort = ReasoningEffort.LOW
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class Completion(Generic[T]):
    output: T
    model_version: str
    raw_content: str


class LlmPort(Protocol):
    async def complete(
        self, request: CompletionRequest, output_type: type[T]
    ) -> Completion[T]: ...

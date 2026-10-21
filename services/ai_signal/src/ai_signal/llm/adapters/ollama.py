"""Ollama (local models) through its OpenAI-compatible ``/v1`` endpoint.

No API key: Ollama ignores the bearer, so a dummy value is sent. Strict
``json_schema`` response_format is honored (Ollama turns it into grammar
constrained decoding), so the shared payload is used unchanged.

Reasoning: gemma4 thinks by default. Checked against Ollama 0.40.6: ``think:
false`` is ignored on ``/v1``, while ``reasoning_effort: "none"`` turns thinking
off (no ``reasoning`` in the reply, about 2 s instead of 12-25 s on a tiny
prompt). So ``off`` maps to ``"none"`` and no ``reasoning`` object is sent.

One local GPU serves one request at a time well, so concurrency defaults to 1
and the timeout is generous. Containers reach the host
Ollama at ``OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`` (Docker
Desktop); the replay runner's hosts file must include a ``host.docker.internal``
entry, otherwise the name does not resolve.
"""

from __future__ import annotations

import httpx
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_signal.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from ai_signal.llm.port import CompletionRequest, ReasoningEffort

_DUMMY_KEY = "ollama"


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_SIGNAL_OLLAMA_",
        env_file=None,
        extra="ignore",
        populate_by_name=True,
    )

    base_url: str = Field(
        default="http://localhost:11434/v1",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "AI_SIGNAL_OLLAMA_BASE_URL", "base_url"),
    )
    max_concurrency: int = Field(
        default=1,
        validation_alias=AliasChoices(
            "OLLAMA_MAX_CONCURRENCY", "AI_SIGNAL_OLLAMA_MAX_CONCURRENCY", "max_concurrency"
        ),
    )
    timeout_seconds: float = 120.0


class OllamaAdapter(OpenAICompatibleAdapter):
    def __init__(
        self, settings: OllamaSettings, *, http: httpx.AsyncClient | None = None
    ) -> None:
        super().__init__(
            api_key=_DUMMY_KEY,
            base_url=settings.base_url,
            max_concurrency=settings.max_concurrency,
            timeout_seconds=settings.timeout_seconds,
            http=http,
        )

    @classmethod
    def from_env(cls) -> OllamaAdapter:
        return cls(OllamaSettings())

    def provider_fields(self, request: CompletionRequest) -> dict[str, object]:
        effort = "none" if request.reasoning is ReasoningEffort.OFF else request.reasoning.value
        return {"reasoning_effort": effort}

"""OpenRouter: ``reasoning`` object with a token cap, strict json_schema."""

from __future__ import annotations

import httpx
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_signal.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from ai_signal.llm.port import CompletionRequest, ReasoningEffort


class OpenRouterSettings(BaseSettings):
    """``OPENROUTER_API_KEY`` and the legacy ``AI_SIGNAL_LLM_*`` names still work."""

    model_config = SettingsConfigDict(
        env_prefix="AI_SIGNAL_OPENROUTER_",
        env_file=None,
        extra="ignore",
        populate_by_name=True,
    )

    api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices(
            "OPENROUTER_API_KEY", "AI_SIGNAL_OPENROUTER_API_KEY", "AI_SIGNAL_LLM_API_KEY"
        ),
    )
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices(
            "AI_SIGNAL_OPENROUTER_BASE_URL", "AI_SIGNAL_LLM_BASE_URL", "base_url"
        ),
    )
    max_concurrency: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "AI_SIGNAL_OPENROUTER_MAX_CONCURRENCY",
            "AI_SIGNAL_LLM_MAX_CONCURRENCY",
            "max_concurrency",
        ),
    )
    reasoning_max_tokens: int = Field(
        default=2048,
        validation_alias=AliasChoices(
            "AI_SIGNAL_OPENROUTER_REASONING_MAX_TOKENS",
            "AI_SIGNAL_LLM_REASONING_MAX_TOKENS",
            "reasoning_max_tokens",
        ),
    )
    timeout_seconds: float = Field(
        default=90.0,
        validation_alias=AliasChoices(
            "AI_SIGNAL_OPENROUTER_TIMEOUT_SECONDS",
            "AI_SIGNAL_LLM_TIMEOUT_SECONDS",
            "timeout_seconds",
        ),
    )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    def __init__(
        self, settings: OpenRouterSettings, *, http: httpx.AsyncClient | None = None
    ) -> None:
        super().__init__(
            api_key=settings.api_key,
            base_url=settings.base_url,
            max_concurrency=settings.max_concurrency,
            timeout_seconds=settings.timeout_seconds,
            http=http,
        )
        self._reasoning_max_tokens = settings.reasoning_max_tokens

    @classmethod
    def from_env(cls) -> OpenRouterAdapter:
        return cls(OpenRouterSettings())

    def provider_fields(self, request: CompletionRequest) -> dict[str, object]:
        if request.reasoning is ReasoningEffort.OFF:
            return {"reasoning": {"enabled": False, "exclude": True}}
        return {
            "reasoning": {"max_tokens": self._reasoning_max_tokens, "exclude": True}
        }

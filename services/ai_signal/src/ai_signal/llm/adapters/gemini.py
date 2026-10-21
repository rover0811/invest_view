"""Gemini OpenAI-compatible endpoint: ``reasoning_effort`` only, no reasoning object.

A 429 is either a per-minute limit (retry soon) or an exhausted daily quota
(skip the model until the quota resets); the body tells them apart.
"""

from __future__ import annotations

import httpx
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_signal.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from ai_signal.llm.port import CompletionRequest, LlmError, LlmErrorKind, ReasoningEffort

_DAILY_MARKERS = ("perday", "per day", "daily")


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_SIGNAL_GEMINI_",
        env_file=None,
        extra="ignore",
        populate_by_name=True,
    )

    api_key: str = Field(
        default="",
        repr=False,
        validation_alias=AliasChoices("GEMINI_API_KEY", "AI_SIGNAL_GEMINI_API_KEY"),
    )
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    max_concurrency: int = 2
    timeout_seconds: float = 90.0


class GeminiAdapter(OpenAICompatibleAdapter):
    def __init__(
        self, settings: GeminiSettings, *, http: httpx.AsyncClient | None = None
    ) -> None:
        super().__init__(
            api_key=settings.api_key,
            base_url=settings.base_url,
            max_concurrency=settings.max_concurrency,
            timeout_seconds=settings.timeout_seconds,
            http=http,
        )

    @classmethod
    def from_env(cls) -> GeminiAdapter:
        return cls(GeminiSettings())

    def provider_fields(self, request: CompletionRequest) -> dict[str, object]:
        effort = "none" if request.reasoning is ReasoningEffort.OFF else request.reasoning.value
        return {"reasoning_effort": effort}

    def map_status(self, response: httpx.Response, model: str) -> LlmError:
        if response.status_code == 429:
            text = response.text.lower().replace("_", "")
            if any(marker in text for marker in _DAILY_MARKERS):
                return LlmError(
                    LlmErrorKind.QUOTA_EXHAUSTED,
                    f"{model} daily quota exhausted",
                    status_code=429,
                )
        return super().map_status(response, model)

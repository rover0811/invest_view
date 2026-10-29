"""Adapter registry: a new provider is one module plus one line here."""

from __future__ import annotations

from collections.abc import Callable

from ai_signal.llm.adapters.gemini import GeminiAdapter
from ai_signal.llm.adapters.ollama import OllamaAdapter
from ai_signal.llm.adapters.openrouter import OpenRouterAdapter
from ai_signal.llm.port import LlmPort

ADAPTERS: dict[str, Callable[[], LlmPort]] = {
    "gemini": GeminiAdapter.from_env,
    "openrouter": OpenRouterAdapter.from_env,
    "ollama": OllamaAdapter.from_env,
}

__all__ = ["ADAPTERS"]

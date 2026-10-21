"""LLM response cache as a port decorator (22).

A replay re-sends the same prompts; ``CachingPort`` answers identical requests
from a store instead of calling the provider again. The key covers everything
that shapes the answer: adapter, model, messages, max_tokens, reasoning and
the output schema. Only successful completions are stored; errors always reach
the caller so route health and fallback behave as without the cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_signal.llm.port import Completion, CompletionRequest, LlmPort

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class CacheEntry:
    adapter: str
    model: str
    raw_content: str
    model_version: str


class CacheStore(Protocol):
    async def get(self, key: str) -> CacheEntry | None: ...

    async def put(self, key: str, entry: CacheEntry) -> None: ...


def cache_key(
    adapter_name: str, request: CompletionRequest, output_type: type[BaseModel]
) -> str:
    payload = {
        "adapter": adapter_name,
        "model": request.model,
        "messages": [dict(message) for message in request.messages],
        "max_tokens": request.max_tokens,
        "reasoning": request.reasoning.value,
        "schema": output_type.model_json_schema(),
    }
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CachingPort:
    inner: LlmPort
    store: CacheStore
    adapter_name: str

    async def complete(
        self, request: CompletionRequest, output_type: type[T]
    ) -> Completion[T]:
        key = cache_key(self.adapter_name, request, output_type)
        entry = await self.store.get(key)
        if entry is not None:
            try:
                output = output_type.model_validate_json(
                    _FENCE.sub("", entry.raw_content.strip())
                )
            except ValidationError:
                logger.warning("cached LLM answer %s no longer validates", key[:12])
            else:
                return Completion(
                    output=output,
                    model_version=entry.model_version,
                    raw_content=entry.raw_content,
                    cached=True,
                )
        completion = await self.inner.complete(request, output_type)
        await self.store.put(
            key,
            CacheEntry(
                adapter=self.adapter_name,
                model=request.model,
                raw_content=completion.raw_content,
                model_version=completion.model_version,
            ),
        )
        return replace(completion, cached=False)


@dataclass
class InMemoryCacheStore:
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    hits: dict[str, int] = field(default_factory=dict)

    async def get(self, key: str) -> CacheEntry | None:
        entry = self.entries.get(key)
        if entry is not None:
            self.hits[key] = self.hits.get(key, 0) + 1
        return entry

    async def put(self, key: str, entry: CacheEntry) -> None:
        self.entries[key] = entry


_GET = text(
    """
    UPDATE gold.llm_cache
       SET hit_count = hit_count + 1, last_hit_at = :now
     WHERE key = :key
    RETURNING adapter, model, raw_content, model_version
    """
)
_PUT = text(
    """
    INSERT INTO gold.llm_cache (key, adapter, model, raw_content, model_version, created_at)
    VALUES (:key, :adapter, :model, :raw_content, :model_version, :now)
    ON CONFLICT (key) DO UPDATE
       SET adapter = EXCLUDED.adapter,
           model = EXCLUDED.model,
           raw_content = EXCLUDED.raw_content,
           model_version = EXCLUDED.model_version,
           created_at = EXCLUDED.created_at,
           hit_count = 0,
           last_hit_at = NULL
    """
)


@dataclass
class PostgresCacheStore:
    """``gold.llm_cache``; replay cleanup leaves this table alone on purpose."""

    engine: AsyncEngine

    async def get(self, key: str) -> CacheEntry | None:
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(_GET, {"key": key, "now": datetime.now(UTC)})
            ).first()
        if row is None:
            return None
        return CacheEntry(
            adapter=row.adapter,
            model=row.model,
            raw_content=row.raw_content,
            model_version=row.model_version,
        )

    async def put(self, key: str, entry: CacheEntry) -> None:
        async with self.engine.begin() as connection:
            _ = await connection.execute(
                _PUT,
                {
                    "key": key,
                    "adapter": entry.adapter,
                    "model": entry.model,
                    "raw_content": entry.raw_content,
                    "model_version": entry.model_version,
                    "now": datetime.now(UTC),
                },
            )

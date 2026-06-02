from __future__ import annotations

from typing import Any

from event_pattern_persistence.kafka.consumer import MessageHandler
from event_pattern_persistence.repository.pattern_events import PatternEventRepository


def make_pattern_handler(repo: PatternEventRepository) -> MessageHandler:
    async def handle_pattern(pattern: dict[str, Any]) -> None:
        await repo.insert(pattern)

    return handle_pattern

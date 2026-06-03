from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportExplicitAny=false, reportAny=false

from typing import Any

from strands.agent.conversation_manager import SlidingWindowConversationManager

from alert_service.agent.session_repo import get_active_path


def to_strands_messages(active_path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert the stored active chat path into Strands message dictionaries."""
    messages: list[dict[str, Any]] = []
    for item in active_path:
        role = item.get("role")
        content = item.get("content")
        status = item.get("status")

        if role not in {"user", "assistant"} or content is None:
            continue
        # Replay all user turns, but only completed assistant turns to avoid partial/failed model context.
        if role == "assistant" and status != "complete":
            continue

        messages.append({"role": role, "content": [{"text": content}]})
    return messages


def build_conversation_manager(window_size: int = 20) -> SlidingWindowConversationManager:
    """Build the Strands sliding-window conversation manager."""
    return SlidingWindowConversationManager(window_size=window_size)


async def load_history_messages(session_factory: Any, session_id: Any) -> list[dict[str, Any]]:
    """Load the active stored path for a session and convert it to Strands messages."""
    async with session_factory() as session:
        active_path = await get_active_path(session, session_id)
    return to_strands_messages(active_path)

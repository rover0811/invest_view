from __future__ import annotations

import logging
from typing import Any

from alert_service.agent.session_repo import (
    get_first_complete_qa_for_untitled_session,
    set_session_title_if_null,
)

logger = logging.getLogger(__name__)


async def generate_title(config: Any, first_question: str, first_answer: str) -> str:
    """MVP deterministic title seam; replace with an LLM-backed summarizer later."""
    del config, first_answer
    title = " ".join(first_question.strip().split())[:30].strip()
    return title or "Untitled chat"


async def maybe_set_title(session_factory: Any, session_id: Any) -> None:
    """Best-effort title generation; never raises into chat streaming."""
    try:
        async with session_factory() as session:
            pair = await get_first_complete_qa_for_untitled_session(session, session_id)
        if pair is None:
            return

        title = await generate_title(None, pair["first_question"], pair["first_answer"])
        async with session_factory() as session:
            await set_session_title_if_null(session, session_id, title)
    except Exception:
        logger.exception("failed to auto-title chat session %s", session_id)

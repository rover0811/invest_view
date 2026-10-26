"""``signal_retry`` worker runner (12.5). Phase 1 is an empty implementation.

Phase 2 reads due ``gold.signal_retry`` rows every 30 seconds, drops rows whose
move has a newer version, and regenerates the rest with exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging

from ai_signal.config import AiSignalSettings

logger = logging.getLogger(__name__)

POLL_SECONDS = 30.0


async def run_signal_retry(settings: AiSignalSettings) -> None:
    _ = settings
    logger.info("signal_retry worker is not implemented in phase 1; idling")
    while True:
        await asyncio.sleep(POLL_SECONDS)

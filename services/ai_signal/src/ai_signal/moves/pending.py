"""Completion of DAILY moves stored before their day's market data.

The move sink stores an intraday-arriving DAILY move with null contributions;
the market data job calls ``complete_pending_moves`` after each load. This
module depends only on ``market_data`` reads, never on the Kafka consumer.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_signal.market_data.repository import KST
from ai_signal.moves.decompose import Decomposition, decompose_daily

logger = logging.getLogger(__name__)


def contrib_params(decomposition: Decomposition | None) -> dict[str, object]:
    if decomposition is None:
        return {
            "contrib_global": None,
            "contrib_market": None,
            "contrib_sector": None,
            "contrib_idio": None,
            "beta_method": None,
            "beta_as_of": None,
            "dominant_layer": None,
        }
    return {
        "contrib_global": decomposition.contrib_global,
        "contrib_market": decomposition.contrib_market,
        "contrib_sector": decomposition.contrib_sector,
        "contrib_idio": decomposition.contrib_idio,
        "beta_method": decomposition.beta_method,
        "beta_as_of": decomposition.beta_as_of,
        "dominant_layer": decomposition.dominant_layer,
    }


FILL_MOVE = text(
    """
    UPDATE gold.move SET
        contrib_global = :contrib_global, contrib_market = :contrib_market,
        contrib_sector = :contrib_sector, contrib_idio = :contrib_idio,
        beta_method = :beta_method, beta_as_of = :beta_as_of,
        dominant_layer = :dominant_layer
    WHERE move_id = :move_id AND version = :version AND contrib_idio IS NULL
    """
)


async def complete_pending_moves(engine: AsyncEngine) -> int:
    """Fill DAILY moves stored before their day's market data was loaded."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT m.move_id, m.version, s.ticker, m.window_end
                FROM gold.move m
                JOIN reference.entity_symbol s ON s.entity_id = m.symbol_entity_id
                WHERE m.move_type = 'DAILY' AND m.contrib_idio IS NULL
                ORDER BY m.window_end
                """
            )
        )
        filled = 0
        for move_id, version, ticker, window_end in result.all():
            day = window_end.astimezone(KST).date()
            decomposition = await decompose_daily(conn, ticker, day)
            if decomposition is None:
                continue
            await conn.execute(
                FILL_MOVE,
                {
                    "move_id": move_id,
                    "version": version,
                    **contrib_params(decomposition),
                },
            )
            filled += 1
    if filled:
        logger.info("filled decomposition for %d pending moves", filled)
    return filled

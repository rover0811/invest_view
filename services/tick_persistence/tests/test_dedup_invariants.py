"""I1 (idempotency) invariant.

INVARIANTS.md I1: the same logical tick may be delivered/replayed N times through different Kafka
offsets, but bronze must store exactly one row for that logical event. Kafka `topic:partition:offset`
is audit lineage only, not business identity.

This property test asserts the invariant from `.sisyphus/evidence/task-1-key-decision.md`: identity is the
content-derived event_id key. We COUNT BY THOSE BUSINESS FIELDS, never by `tick_dedupe_key`, so the test
verifies the invariant rather than mirroring an offset-based implementation.
"""
from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession

from tests.strategies import COMMON_HYPOTHESIS_SETTINGS, hypothesis_db_session, stock_tick
from tick_persistence.kafka.consumer import TickMessage
from tick_persistence.repository.tick_history import TickHistoryRepository

pytestmark = pytest.mark.qa


class _RollbackExample(Exception):
    pass


_IDENTITY_FIELDS: tuple[str, ...] = (
    "market",
    "symbol",
    "business_date",
    "cumulative_volume",
    "trade_time",
    "price",
    "trade_type",
)


async def _bronze_total(session: AsyncSession) -> int:
    count = await session.scalar(sa.text("SELECT count(*) FROM bronze.tick_history"))
    return int(count or 0)


async def _count_logical_rows(session: AsyncSession, tick: dict[str, Any]) -> int:
    predicate = " AND ".join(f"{field} = :{field}" for field in _IDENTITY_FIELDS)
    params = {field: tick[field] for field in _IDENTITY_FIELDS}
    count = await session.scalar(
        sa.text(f"SELECT count(*) FROM bronze.tick_history WHERE {predicate}"),
        params,
    )
    return int(count or 0)


@COMMON_HYPOTHESIS_SETTINGS
@given(tick=stock_tick(), reps=st.integers(min_value=2, max_value=5))
async def test_i1_same_logical_tick_persists_exactly_one_row(
    hypothesis_db_session: AsyncSession,
    tick: dict[str, Any],
    reps: int,
) -> None:
    assert await _bronze_total(hypothesis_db_session) == 0

    repo = TickHistoryRepository()
    try:
        async with hypothesis_db_session.begin_nested():
            for rep in range(reps):
                # Same logical tick, fresh Kafka lineage each time: vary BOTH partition and offset so
                # every republish yields a distinct topic:partition:offset (the current dedupe key).
                message = TickMessage(
                    value=tick,
                    topic="stock-ticks",
                    partition=rep,
                    offset=1000 + rep * 13,
                    headers={},
                )
                _ = await repo.insert(hypothesis_db_session, message)

            rows = await _count_logical_rows(hypothesis_db_session, tick)
            assert rows == 1, (
                f"I1 violated: one logical tick republished at {reps} Kafka offsets produced {rows} bronze "
                f"rows (expected 1). topic:partition:offset is audit lineage, not business identity."
            )
            raise _RollbackExample
    except _RollbackExample:
        pass

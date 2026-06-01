"""Repository QA tests against a real testcontainers Postgres migrated via alembic upgrade head."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from event_pattern_persistence.db.models import PatternEvent
from event_pattern_persistence.repository.pattern_events import PatternEventRepository


pytestmark = pytest.mark.qa


def _pattern(pattern_event_id: str | None = None, triggered_ms: int = 1748736300000) -> dict:
    return {
        "pattern_event_id": pattern_event_id or str(uuid.uuid4()),
        "symbol": "005930",
        "market": "KRX",
        "pattern_type": "GOLDEN_CROSS",
        "window_start": 1748736000000,
        "window_end": 1748736300000,
        "triggered_at": triggered_ms,
        "trigger_values": {"short_ma": "70000", "long_ma": "69500"},
        "strategy_name": "ma_cross",
        "source_tick_event_id": None,
    }


async def test_migration_creates_gold_pattern_events(db_session_factory):
    async with db_session_factory() as session:
        reg = await session.execute(sa.text("SELECT to_regclass('gold.pattern_events')"))
        assert reg.scalar_one() == "gold.pattern_events"


async def test_insert_maps_pattern_dict_to_row(db_session_factory):
    repo = PatternEventRepository(db_session_factory)
    pid = str(uuid.uuid4())
    await repo.insert(_pattern(pattern_event_id=pid))

    async with db_session_factory() as session:
        row = await session.get(PatternEvent, uuid.UUID(pid))
    assert row is not None
    assert row.symbol == "005930"
    assert row.market == "KRX"
    assert row.pattern_type == "GOLDEN_CROSS"
    assert row.strategy_name == "ma_cross"
    assert row.trigger_values == {"short_ma": "70000", "long_ma": "69500"}
    assert row.triggered_at.tzinfo is not None


async def test_duplicate_pattern_event_id_inserts_one_row(db_session_factory):
    repo = PatternEventRepository(db_session_factory)
    pid = str(uuid.uuid4())
    payload = _pattern(pattern_event_id=pid)

    await repo.insert(payload)
    await repo.insert(payload)

    async with db_session_factory() as session:
        count = await session.execute(
            sa.text("SELECT count(*) FROM gold.pattern_events WHERE pattern_event_id = :pid"),
            {"pid": pid},
        )
    assert count.scalar_one() == 1


async def test_timestamp_millis_coerced_to_utc(db_session_factory):
    repo = PatternEventRepository(db_session_factory)
    pid = str(uuid.uuid4())
    await repo.insert(_pattern(pattern_event_id=pid, triggered_ms=1748736300000))

    async with db_session_factory() as session:
        row = await session.get(PatternEvent, uuid.UUID(pid))
    assert row is not None
    assert row.triggered_at == datetime.fromtimestamp(1748736300000 / 1000, tz=timezone.utc)

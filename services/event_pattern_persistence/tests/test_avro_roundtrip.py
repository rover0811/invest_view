"""Avro round-trip via fastavro (the codec confluent's AvroDeserializer delegates to): encode -> decode -> repo coercion. Validates timestamp-millis -> datetime and enum/map handling."""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastavro import parse_schema, schemaless_reader, schemaless_writer

from event_pattern_persistence.repository.pattern_events import _to_row

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "stock-patterns.avsc"
_SCHEMA = parse_schema(json.loads(_SCHEMA_PATH.read_text()))


def _roundtrip(payload: dict) -> dict:
    buf = io.BytesIO()
    schemaless_writer(buf, _SCHEMA, payload)
    buf.seek(0)
    return schemaless_reader(buf, _SCHEMA)


def _payload(pattern_event_id: str, triggered_at: datetime) -> dict:
    return {
        "pattern_event_id": pattern_event_id,
        "symbol": "005930",
        "market": "KRX",
        "pattern_type": "GOLDEN_CROSS",
        "window_start": datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc),
        "window_end": datetime(2026, 6, 1, 0, 5, 0, tzinfo=timezone.utc),
        "triggered_at": triggered_at,
        "trigger_values": {"short_ma": "70000", "long_ma": "69500"},
        "strategy_name": "ma_cross",
        "source_tick_event_id": None,
    }


def test_avro_roundtrip_produces_insertable_dict():
    pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "GOLDEN_CROSS:005930:1748736300000"))
    triggered_at = datetime(2026, 6, 1, 0, 5, 0, tzinfo=timezone.utc)

    out = _roundtrip(_payload(pid, triggered_at))

    assert out["pattern_event_id"] == pid
    assert out["pattern_type"] == "GOLDEN_CROSS"
    assert out["trigger_values"] == {"short_ma": "70000", "long_ma": "69500"}
    assert isinstance(out["triggered_at"], datetime)

    row = _to_row(out)
    assert row["pattern_event_id"] == uuid.UUID(pid)
    assert row["triggered_at"] == triggered_at
    assert row["triggered_at"].tzinfo is not None
    assert row["market"] == "KRX"
    assert row["source_tick_event_id"] is None


@pytest.mark.qa
async def test_avro_roundtrip_persists_to_gold(db_session_factory):
    from event_pattern_persistence.db.models import PatternEvent
    from event_pattern_persistence.repository.pattern_events import PatternEventRepository

    pid = str(uuid.uuid4())
    triggered_at = datetime(2026, 6, 1, 0, 5, 0, tzinfo=timezone.utc)

    out = _roundtrip(_payload(pid, triggered_at))
    repo = PatternEventRepository(db_session_factory)
    await repo.insert(out)

    async with db_session_factory() as session:
        row = await session.get(PatternEvent, uuid.UUID(pid))
    assert row is not None
    assert row.pattern_type == "GOLDEN_CROSS"
    assert row.triggered_at == triggered_at

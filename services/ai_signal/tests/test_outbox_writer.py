from __future__ import annotations

from datetime import UTC, datetime

import asyncio
from uuid import UUID

import pytest

from ai_signal.avro import (
    AvroValueDeserializer,
    AvroValueSerializer,
    SchemaNotFoundError,
    record_name,
    schema_path,
)
from ai_signal.outbox import OutboxWriter
from tests.fakes import (
    FakeSerializer,
    FakeSession,
    as_session,
    compiled_params,
    compiled_sql,
    mock_registry,
)

EVENT_ID = UUID("00000000-0000-4000-8000-000000000001")


def test_write_inserts_debezium_outbox_row() -> None:
    session = FakeSession()
    writer = OutboxWriter(as_session(session), FakeSerializer())

    event_id = asyncio.run(
        writer.write(
            topic="signal-requests",
            key="000660",
            value={
                "symbol": "000660",
                "reason": "NEW_DOCUMENT",
                "event_time": "2026-10-20T01:05:00Z",
            },
            event_id=EVENT_ID,
        )
    )

    assert event_id == EVENT_ID
    (statement,) = session.statements
    params = compiled_params(statement)
    assert params["id"] == EVENT_ID
    assert params["aggregatetype"] == "signal-requests"
    assert params["aggregateid"] == "000660"
    assert params["type"] == "Record<signal-requests>"
    assert params["payload"] == FakeSerializer().serialize(
        "signal-requests",
        {
            "symbol": "000660",
            "reason": "NEW_DOCUMENT",
            "event_time": "2026-10-20T01:05:00Z",
        },
    )
    sql = compiled_sql(statement)
    assert "INSERT INTO outbox.events" in sql
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert session.committed == 0


def test_write_generates_distinct_ids_and_accepts_event_type() -> None:
    session = FakeSession()
    writer = OutboxWriter(as_session(session), FakeSerializer())

    async def write_two() -> tuple[UUID, UUID]:
        first = await writer.write(topic="t", key="k", value={})
        second = await writer.write(topic="t", key="k", value={}, event_type="Custom")
        return first, second

    first, second = asyncio.run(write_two())
    assert first != second
    assert compiled_params(session.statements[1])["type"] == "Custom"


def test_avro_serializer_round_trip_uses_registered_schema() -> None:
    client = mock_registry("signal-requests")
    serializer = AvroValueSerializer(client)
    value = {
        "symbol": "005930",
        "reason": "PRICE_STATE_CHANGE",
        "event_time": datetime(2026, 10, 20, 1, 5, tzinfo=UTC),
    }

    payload = serializer.serialize("signal-requests", value)

    assert payload[0] == 0
    schema_id = int.from_bytes(payload[1:5], "big")
    assert client.get_latest_version("signal-requests-value").schema_id == schema_id
    assert (
        AvroValueDeserializer(client).deserialize("signal-requests", payload) == value
    )
    assert serializer.record_name("signal-requests") == "SignalRequest"


def test_avro_serializer_does_not_auto_register() -> None:
    serializer = AvroValueSerializer(mock_registry())
    with pytest.raises(Exception, match="not found|40403|Schema"):
        _ = serializer.serialize(
            "signal-requests", {"symbol": "1", "reason": "NEW_DOCUMENT"}
        )


def test_schema_lookup() -> None:
    assert schema_path("signal-dlq").name == "signal-requests.avsc"
    assert record_name("signal-updates") == "SignalUpdate"
    with pytest.raises(SchemaNotFoundError):
        _ = schema_path("no-such-topic")

from __future__ import annotations

from datetime import UTC, datetime

import asyncio

from ai_signal.avro import AvroValueDeserializer, AvroValueSerializer
from ai_signal.outbox import OutboxWriter
from ai_signal.signal_requests import (
    SIGNAL_REQUESTS_TOPIC,
    RequestTrigger,
    SignalRequestReason,
    publish_signal_requests,
    request_targets,
)
from tests.fakes import FakeSession, as_session, compiled_params, mock_registry

EVENT_TIME = datetime(2026, 10, 20, 1, 5, tzinfo=UTC)


def test_phase1_targets_are_the_trigger_symbols_in_order() -> None:
    session = as_session(FakeSession())
    trigger = RequestTrigger(scope="MARKET", symbols=("000660", "005930", "000660", ""))
    assert asyncio.run(request_targets(session, trigger)) == ("000660", "005930")


def test_publish_writes_one_avro_event_per_symbol() -> None:
    client = mock_registry(SIGNAL_REQUESTS_TOPIC)
    session = FakeSession()
    writer = OutboxWriter(as_session(session), AvroValueSerializer(client))

    ids = asyncio.run(
        publish_signal_requests(
            writer,
            ["005930", "005930", "000660"],
            SignalRequestReason.NEW_DOCUMENT,
            EVENT_TIME,
        )
    )

    assert len(ids) == 2
    params = [compiled_params(statement) for statement in session.statements]
    assert [p["aggregateid"] for p in params] == ["005930", "000660"]
    assert {p["aggregatetype"] for p in params} == {SIGNAL_REQUESTS_TOPIC}
    assert {p["type"] for p in params} == {"SignalRequest"}
    payload = params[1]["payload"]
    assert isinstance(payload, bytes)
    decoded = AvroValueDeserializer(client).deserialize(SIGNAL_REQUESTS_TOPIC, payload)
    assert decoded == {
        "symbol": "000660",
        "reason": "NEW_DOCUMENT",
        "event_time": EVENT_TIME,
    }

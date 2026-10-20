from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from confluent_kafka import KafkaError, Message

from ai_signal.outbox.relay import (
    DeliveryCallback,
    HeaderList,
    OutboxRecord,
    OutboxRelay,
    OutboxRelayError,
    send_records,
    to_record,
)
from tests.fakes import FakeResult, FakeSessionFactory, compiled_sql

EVENT_ID = UUID("00000000-0000-4000-8000-000000000002")
CREATED_AT = datetime(2026, 10, 19, 6, 30, tzinfo=UTC)


@dataclass
class FakeProducer:
    fail_topics: set[str] = field(default_factory=set)
    in_flight_after_flush: int = 0
    buffer_errors: int = 0
    produced: list[dict[str, object]] = field(default_factory=list)
    pending: list[Callable[[], None]] = field(default_factory=list)

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: bytes | None = None,
        partition: int = -1,
        callback: DeliveryCallback | None = None,
        on_delivery: DeliveryCallback | None = None,
        timestamp: int = 0,
        headers: HeaderList | None = None,
    ) -> None:
        if self.buffer_errors:
            self.buffer_errors -= 1
            raise BufferError("queue full")
        assert on_delivery is not None
        report = on_delivery
        self.produced.append(
            {
                "topic": topic,
                "value": value,
                "key": key,
                "headers": headers,
                "timestamp": timestamp,
            }
        )
        error = (
            KafkaError(KafkaError._MSG_TIMED_OUT) if topic in self.fail_topics else None
        )
        message = Message(topic=topic, key=key, value=value)
        self.pending.append(lambda: report(error, message))

    def poll(self, timeout: float) -> int:
        return 0

    def flush(self, timeout: float) -> int:
        for deliver in self.pending:
            deliver()
        self.pending.clear()
        return self.in_flight_after_flush


def _record(topic: str = "signal-updates") -> OutboxRecord:
    return to_record(
        event_id=EVENT_ID,
        aggregatetype=topic,
        aggregateid="000660",
        event_type="SignalUpdate",
        payload=b"\x00\x00\x00\x00\x01avro",
        created_at=CREATED_AT,
    )


def test_to_record_matches_outbox_event_router() -> None:
    record = _record()
    assert record.topic == "signal-updates"
    assert record.key == b"000660"
    assert record.value == b"\x00\x00\x00\x00\x01avro"
    assert record.headers == (
        ("id", str(EVENT_ID).encode()),
        ("type", b"SignalUpdate"),
    )
    assert record.timestamp_ms == int(CREATED_AT.timestamp() * 1000)


def test_send_records_retries_buffer_errors_and_succeeds() -> None:
    producer = FakeProducer(buffer_errors=2)
    send_records(producer, [_record(), _record("signal-requests")])
    assert [item["topic"] for item in producer.produced] == [
        "signal-updates",
        "signal-requests",
    ]
    assert producer.produced[0]["headers"] == list(_record().headers)


@pytest.mark.parametrize(
    "producer",
    [
        FakeProducer(fail_topics={"signal-updates"}),
        FakeProducer(in_flight_after_flush=1),
    ],
)
def test_send_records_raises_on_any_undelivered(producer: FakeProducer) -> None:
    with pytest.raises(OutboxRelayError):
        send_records(producer, [_record()])


def _row(event_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=event_id,
        aggregatetype="signal-requests",
        aggregateid="005930",
        type="SignalRequest",
        payload=b"bytes",
        created_at=CREATED_AT,
    )


def test_relay_once_deletes_only_after_delivery() -> None:
    factory = FakeSessionFactory(results=[FakeResult([_row(EVENT_ID)])])
    producer = FakeProducer()
    relay = OutboxRelay(factory.as_factory(), producer)

    assert asyncio.run(relay.relay_once()) == 1

    (session,) = factory.sessions
    select_sql, delete_sql = (compiled_sql(stmt) for stmt in session.statements)
    assert "FOR UPDATE SKIP LOCKED" in select_sql
    assert "ORDER BY outbox.events.created_at, outbox.events.id" in select_sql
    assert delete_sql.startswith("DELETE FROM outbox.events")
    assert session.committed == 1
    assert producer.produced[0]["key"] == b"005930"


def test_relay_once_keeps_rows_when_delivery_fails() -> None:
    factory = FakeSessionFactory(results=[FakeResult([_row(EVENT_ID)])])
    relay = OutboxRelay(
        factory.as_factory(), FakeProducer(fail_topics={"signal-requests"})
    )

    with pytest.raises(OutboxRelayError):
        _ = asyncio.run(relay.relay_once())

    (session,) = factory.sessions
    assert len(session.statements) == 1
    assert session.rolled_back == 1
    assert session.committed == 0


def test_relay_once_with_empty_outbox_sends_nothing() -> None:
    factory = FakeSessionFactory()
    producer = FakeProducer()
    assert asyncio.run(OutboxRelay(factory.as_factory(), producer).relay_once()) == 0
    assert producer.produced == []


def test_run_stops_on_event() -> None:
    factory = FakeSessionFactory()
    relay = OutboxRelay(factory.as_factory(), FakeProducer())

    async def run_then_stop() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(relay.run(stop, idle_seconds=0.01))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run_then_stop())
    assert len(factory.sessions) >= 2

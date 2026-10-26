"""Temporary outbox relay, replaced by Debezium in phase 2 (16.5, 18.6).

Each produced record is what the Debezium Outbox Event Router emits with this
connector configuration, so consumers do not change when the relay is switched off::

    transforms=outbox
    transforms.outbox.type=io.debezium.transforms.outbox.EventRouter
    transforms.outbox.route.topic.replacement=${routedByValue}
    transforms.outbox.table.fields.additional.placement=type:header:type
    key.converter=org.apache.kafka.connect.storage.StringConverter
    value.converter=io.debezium.converters.BinaryDataConverter

- topic: ``aggregatetype``
- key: ``aggregateid`` as UTF-8
- value: ``payload`` bytes unchanged (already Schema Registry Avro)
- headers: ``id`` (event id, router default) and ``type``
- timestamp: ``created_at``

Delivery is at least once. One batch is locked with ``FOR UPDATE SKIP LOCKED``,
produced, flushed, and only after every delivery report succeeds the rows are
deleted in the same transaction. A crash between produce and commit leaves the
rows in place, so they are sent again; consumers already tolerate duplicates (16.4).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from confluent_kafka import KafkaError, Message, Producer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_signal.config import AiSignalSettings
from ai_signal.db import create_engine, create_session_factory
from ai_signal.outbox.writer import OUTBOX_EVENTS

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
DEFAULT_IDLE_SECONDS = 1.0
DEFAULT_FLUSH_TIMEOUT_SECONDS = 30.0


class OutboxRelayError(RuntimeError):
    pass


@dataclass(frozen=True)
class OutboxRecord:
    event_id: UUID
    topic: str
    key: bytes
    value: bytes
    headers: tuple[tuple[str, bytes], ...]
    timestamp_ms: int


HeaderList = list[tuple[str, str | bytes | None]]
DeliveryCallback = Callable[[KafkaError | None, Message], None]


class ProducerLike(Protocol):
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
    ) -> None: ...

    def poll(self, timeout: float) -> int: ...

    def flush(self, timeout: float) -> int: ...


def to_record(
    *,
    event_id: UUID,
    aggregatetype: str,
    aggregateid: str,
    event_type: str,
    payload: bytes,
    created_at: datetime,
) -> OutboxRecord:
    return OutboxRecord(
        event_id=event_id,
        topic=aggregatetype,
        key=aggregateid.encode("utf-8"),
        value=bytes(payload),
        headers=(
            ("id", str(event_id).encode("utf-8")),
            ("type", event_type.encode("utf-8")),
        ),
        timestamp_ms=int(created_at.timestamp() * 1000),
    )


def _header_list(record: OutboxRecord) -> HeaderList:
    return [(name, value) for name, value in record.headers]


def create_producer(bootstrap_servers: str) -> Producer:
    return Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "linger.ms": 5,
        }
    )


def send_records(
    producer: ProducerLike,
    records: Sequence[OutboxRecord],
    flush_timeout: float = DEFAULT_FLUSH_TIMEOUT_SECONDS,
) -> None:
    """Produce and flush; raise unless every record was acknowledged."""
    failures: list[str] = []
    delivered = 0

    def on_delivery(error: KafkaError | None, message: Message) -> None:
        nonlocal delivered
        if error is not None:
            failures.append(f"{message.topic()}: {error}")
        else:
            delivered += 1

    for record in records:
        while True:
            try:
                producer.produce(
                    record.topic,
                    value=record.value,
                    key=record.key,
                    headers=_header_list(record),
                    timestamp=record.timestamp_ms,
                    on_delivery=on_delivery,
                )
                break
            except BufferError:
                _ = producer.poll(0.5)
    remaining = producer.flush(flush_timeout)
    if remaining > 0 or failures or delivered != len(records):
        raise OutboxRelayError(
            f"outbox delivery incomplete: delivered={delivered}/{len(records)}"
            + f" in_flight={remaining} failures={failures[:3]}"
        )


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        producer: ProducerLike,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_timeout: float = DEFAULT_FLUSH_TIMEOUT_SECONDS,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._session_factory = session_factory
        self._producer = producer
        self._batch_size = batch_size
        self._flush_timeout = flush_timeout

    async def relay_once(self) -> int:
        """Send one batch of unsent events and delete them; return the count."""
        select_batch = (
            sa.select(
                OUTBOX_EVENTS.c.id,
                OUTBOX_EVENTS.c.aggregatetype,
                OUTBOX_EVENTS.c.aggregateid,
                OUTBOX_EVENTS.c.type,
                OUTBOX_EVENTS.c.payload,
                OUTBOX_EVENTS.c.created_at,
            )
            .order_by(OUTBOX_EVENTS.c.created_at, OUTBOX_EVENTS.c.id)
            .limit(self._batch_size)
            .with_for_update(skip_locked=True)
        )
        async with self._session_factory() as session, session.begin():
            rows = (await session.execute(select_batch)).all()
            if not rows:
                return 0
            records = [
                to_record(
                    event_id=row.id,
                    aggregatetype=row.aggregatetype,
                    aggregateid=row.aggregateid,
                    event_type=row.type,
                    payload=row.payload,
                    created_at=row.created_at,
                )
                for row in rows
            ]
            await asyncio.get_running_loop().run_in_executor(
                None, send_records, self._producer, records, self._flush_timeout
            )
            _ = await session.execute(
                sa.delete(OUTBOX_EVENTS).where(
                    OUTBOX_EVENTS.c.id.in_([record.event_id for record in records])
                )
            )
        logger.info("outbox relay sent %s events", len(records))
        return len(records)

    async def run(
        self,
        stop: asyncio.Event | None = None,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
    ) -> None:
        stop_event = stop if stop is not None else asyncio.Event()
        while not stop_event.is_set():
            if await self.relay_once() >= self._batch_size:
                continue
            try:
                _ = await asyncio.wait_for(stop_event.wait(), timeout=idle_seconds)
            except TimeoutError:
                continue


async def run_outbox_relay(settings: AiSignalSettings) -> None:
    engine = create_engine(settings.database_url)
    producer = create_producer(settings.kafka_bootstrap_servers)
    try:
        await OutboxRelay(create_session_factory(engine), producer).run()
    finally:
        _ = producer.flush(DEFAULT_FLUSH_TIMEOUT_SECONDS)
        await engine.dispose()

"""Integration tests against real Postgres, Kafka, and Schema Registry (kind).

Run with ``AI_SIGNAL_IT_DATABASE_URL`` (asyncpg URL of an admin database),
``AI_SIGNAL_IT_KAFKA_BOOTSTRAP_SERVERS`` and ``AI_SIGNAL_IT_SCHEMA_REGISTRY_URL``.
Each test module run creates and drops its own database, so the shared
``invest_view`` database is never touched. Subjects must already be registered
(``make schemas``).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from confluent_kafka import Consumer, Message, Producer, TopicPartition
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ai_signal.avro import (
    AvroValueDeserializer,
    AvroValueSerializer,
    schema_registry_client,
)
from ai_signal.consumers import (
    ConsumedMessage,
    ConsumerLoop,
    ConsumerSpec,
    HandlerContext,
)
from ai_signal.consumers.frame import ConsumerHandlerError
from ai_signal.db import create_session_factory
from ai_signal.outbox import OUTBOX_EVENTS, OutboxWriter
from ai_signal.outbox.relay import OutboxRelay, create_producer
from ai_signal.signal_requests import SignalRequestReason, publish_signal_requests
from alembic import command

ADMIN_URL = os.environ.get("AI_SIGNAL_IT_DATABASE_URL")
BOOTSTRAP = os.environ.get("AI_SIGNAL_IT_KAFKA_BOOTSTRAP_SERVERS")
SR_URL = os.environ.get("AI_SIGNAL_IT_SCHEMA_REGISTRY_URL")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and BOOTSTRAP and SR_URL),
    reason="AI_SIGNAL_IT_* environment variables are not set",
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]
ENTITY = sa.table(
    "entity",
    sa.column("entity_id"),
    sa.column("entity_type"),
    sa.column("name"),
    schema="reference",
)


async def _admin(statement: str) -> None:
    assert ADMIN_URL is not None
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        _ = await connection.execute(sa.text(statement))
    await engine.dispose()


@pytest.fixture(scope="module")
def database_url(monkeypatch_module: pytest.MonkeyPatch) -> Iterator[str]:
    assert ADMIN_URL is not None
    name = f"ai_signal_it_{uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch_module.setenv("AI_SIGNAL_DATABASE_URL", url)
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
    command.upgrade(config, "head")
    try:
        yield url
    finally:
        asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    with pytest.MonkeyPatch.context() as patch:
        yield patch


def _session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(create_async_engine(url, poolclass=NullPool))


def _end_offsets(topic: str) -> list[TopicPartition]:
    assert BOOTSTRAP is not None
    probe = Consumer(
        {"bootstrap.servers": BOOTSTRAP, "group.id": f"it-probe-{uuid4().hex}"}
    )
    try:
        metadata = probe.list_topics(topic, timeout=10)
        partitions = sorted(metadata.topics[topic].partitions)
        offsets: list[TopicPartition] = []
        for partition in partitions:
            _, high = probe.get_watermark_offsets(
                TopicPartition(topic, partition), timeout=10
            )
            offsets.append(TopicPartition(topic, partition, high))
        return offsets
    finally:
        probe.close()


def _read_from(
    offsets: list[TopicPartition], wanted: int, timeout_s: float = 20.0
) -> list[Message]:
    assert BOOTSTRAP is not None
    reader = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"it-reader-{uuid4().hex}",
            "enable.auto.commit": False,
        }
    )
    reader.assign(offsets)
    found: list[Message] = []
    deadline = time.monotonic() + timeout_s
    try:
        while len(found) < wanted and time.monotonic() < deadline:
            message = reader.poll(1.0)
            if message is not None and message.error() is None:
                found.append(message)
        return found
    finally:
        reader.close()


def _headers(message: Message) -> dict[str, bytes]:
    raw = message.headers() or []
    items = raw.items() if isinstance(raw, dict) else raw
    return {
        name: value.encode() if isinstance(value, str) else value or b""
        for name, value in items
    }


async def _outbox_count(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(OUTBOX_EVENTS)
        )
        return int(count or 0)


def test_outbox_write_relay_and_consume_back(database_url: str) -> None:
    assert BOOTSTRAP is not None and SR_URL is not None
    client = schema_registry_client(SR_URL)
    serializer = AvroValueSerializer(client)
    factory = _session_factory(database_url)
    update = {
        "symbol": "000660",
        "symbol_name": "SK하이닉스",
        "move_id": str(uuid4()),
        "version": 1,
        "status": "SHOWN",
        "headline_label": "it",
        "generation_method": "llm",
        "causes": [
            {
                "rank": 1,
                "explanation": "integration",
                "source_count": 1,
                "source_document_ids": [str(uuid4())],
            }
        ],
        "updated_at": 1_760_000_000_000,
    }
    before = _end_offsets("signal-updates") + _end_offsets("signal-requests")

    async def write() -> tuple[UUID, tuple[UUID, ...]]:
        async with factory() as session, session.begin():
            writer = OutboxWriter(session, serializer)
            update_id = await writer.write(
                topic="signal-updates", key="000660", value=update
            )
            request_ids = await publish_signal_requests(
                writer, ["005930"], SignalRequestReason.NEW_DOCUMENT
            )
        return update_id, request_ids

    update_id, (request_id,) = asyncio.run(write())
    relay = OutboxRelay(factory, create_producer(BOOTSTRAP))

    assert asyncio.run(relay.relay_once()) == 2
    assert asyncio.run(_outbox_count(factory)) == 0
    assert asyncio.run(relay.relay_once()) == 0

    by_id = {_headers(m)["id"].decode(): m for m in _read_from(before, wanted=2)}
    deserializer = AvroValueDeserializer(client)

    update_message = by_id[str(update_id)]
    assert update_message.topic() == "signal-updates"
    assert update_message.key() == b"000660"
    assert _headers(update_message) == {
        "id": str(update_id).encode(),
        "type": b"SignalUpdate",
    }
    value = update_message.value()
    assert value is not None
    decoded = deserializer.deserialize("signal-updates", value)
    assert decoded["move_id"] == update["move_id"]
    assert decoded["causes"] == update["causes"]

    request_message = by_id[str(request_id)]
    assert request_message.topic() == "signal-requests"
    assert request_message.key() == b"005930"
    assert _headers(request_message)["type"] == b"SignalRequest"
    request_value = request_message.value()
    assert request_value is not None
    assert deserializer.deserialize("signal-requests", request_value) == {
        "symbol": "005930",
        "reason": "NEW_DOCUMENT",
    }


def _produce_request(symbol: str) -> TopicPartition:
    assert BOOTSTRAP is not None and SR_URL is not None
    payload = AvroValueSerializer(schema_registry_client(SR_URL)).serialize(
        "signal-requests", {"symbol": symbol, "reason": "PRICE_STATE_CHANGE"}
    )
    reports: list[TopicPartition] = []

    def on_delivery(error: object, message: Message) -> None:
        assert error is None
        partition, offset = message.partition(), message.offset()
        assert partition is not None and offset is not None
        reports.append(TopicPartition("signal-requests", partition, offset))

    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    producer.produce(
        "signal-requests", value=payload, key=symbol.encode(), on_delivery=on_delivery
    )
    assert producer.flush(10) == 0
    (position,) = reports
    return position


def _frame_consumer(group: str, position: TopicPartition) -> tuple[Consumer, Message]:
    assert BOOTSTRAP is not None
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )
    consumer.assign([position])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        message = consumer.poll(1.0)
        if message is not None and message.error() is None:
            return consumer, message
    raise AssertionError("produced message was not consumed")


def _committed(consumer: Consumer, position: TopicPartition) -> int:
    tp = TopicPartition(position.topic, position.partition)
    (committed,) = consumer.committed([tp], timeout=10)
    return committed.offset


def test_consumer_frame_commits_data_outbox_and_offset_together(
    database_url: str,
) -> None:
    assert BOOTSTRAP is not None and SR_URL is not None
    client = schema_registry_client(SR_URL)
    factory = _session_factory(database_url)
    entity_id = uuid4()
    symbol = f"IT{uuid4().hex[:6]}"

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        _ = await context.session.execute(
            sa.insert(ENTITY).values(
                entity_id=entity_id, entity_type="org", name=symbol
            )
        )
        _ = await context.outbox.write(
            topic="signal-updates",
            key=str(message.value["symbol"]),
            value={
                "symbol": message.value["symbol"],
                "move_id": str(entity_id),
                "version": 1,
                "status": "HIDDEN",
                "updated_at": 1_760_000_000_000,
            },
        )

    position = _produce_request(symbol)
    group = f"it-frame-{uuid4().hex}"
    spec = ConsumerSpec(
        name="it", topic="signal-requests", handler=handler, group=group
    )
    consumer, message = _frame_consumer(group, position)
    loop = ConsumerLoop(
        spec,
        consumer=consumer,
        deserializer=AvroValueDeserializer(client),
        serializer=AvroValueSerializer(client),
        session_factory=factory,
    )
    before = _end_offsets("signal-updates")
    try:
        assert message.key() == symbol.encode()
        asyncio.run(loop.process(message))
        assert _committed(consumer, position) == (position.offset or 0) + 1
    finally:
        consumer.close()

    async def stored() -> tuple[int, int]:
        async with factory() as session:
            entities = await session.scalar(
                sa.select(sa.func.count())
                .select_from(ENTITY)
                .where(ENTITY.c.name == symbol)
            )
        return int(entities or 0), await _outbox_count(factory)

    assert asyncio.run(stored()) == (1, 1)
    assert (
        asyncio.run(OutboxRelay(factory, create_producer(BOOTSTRAP)).relay_once()) == 1
    )
    (relayed,) = _read_from(before, wanted=1)
    assert relayed.key() == symbol.encode()
    value = relayed.value()
    assert value is not None
    assert (
        AvroValueDeserializer(client).deserialize("signal-updates", value)["status"]
        == "HIDDEN"
    )


def test_consumer_frame_rolls_back_and_keeps_offset_on_failure(
    database_url: str,
) -> None:
    assert BOOTSTRAP is not None and SR_URL is not None
    client = schema_registry_client(SR_URL)
    factory = _session_factory(database_url)
    symbol = f"IT{uuid4().hex[:6]}"

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        _ = await context.session.execute(
            sa.insert(ENTITY).values(entity_id=uuid4(), entity_type="org", name=symbol)
        )
        _ = await context.outbox.write(
            topic="signal-requests",
            key=symbol,
            value={"symbol": symbol, "reason": "MARKET_CLOSE"},
        )
        raise RuntimeError("handler failure after writes")

    position = _produce_request(symbol)
    group = f"it-frame-{uuid4().hex}"
    spec = ConsumerSpec(
        name="it", topic="signal-requests", handler=handler, group=group
    )
    consumer, message = _frame_consumer(group, position)
    loop = ConsumerLoop(
        spec,
        consumer=consumer,
        deserializer=AvroValueDeserializer(client),
        serializer=AvroValueSerializer(client),
        session_factory=factory,
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    try:
        with pytest.raises(ConsumerHandlerError):
            asyncio.run(loop.process(message))
        assert _committed(consumer, position) < 0
    finally:
        consumer.close()

    async def stored() -> int:
        async with factory() as session:
            count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(ENTITY)
                .where(ENTITY.c.name == symbol)
            )
        return int(count or 0)

    assert asyncio.run(stored()) == 0
    assert asyncio.run(_outbox_count(factory)) == 0

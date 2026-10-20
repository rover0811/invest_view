"""Document processor against real Postgres, Kafka, and Schema Registry (kind).

Same environment as ``test_outbox_integration.py``: ``AI_SIGNAL_IT_DATABASE_URL``,
``AI_SIGNAL_IT_KAFKA_BOOTSTRAP_SERVERS``, ``AI_SIGNAL_IT_SCHEMA_REGISTRY_URL``.
The LLM is a scripted judge, so the test makes no OpenRouter calls.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from confluent_kafka import Consumer, Message, Producer, TopicPartition
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from ai_signal.avro import (
    AvroValueDeserializer,
    AvroValueSerializer,
    schema_registry_client,
)
from ai_signal.consumers import ConsumerLoop, ConsumerSpec
from ai_signal.db import create_session_factory
from ai_signal.documents.news import NEWS_RAW_TOPIC, news_idempotency_key
from ai_signal.documents.processor import DocumentProcessor
from ai_signal.outbox.relay import OutboxRelay, create_producer
from alembic import command
from tests.document_fakes import judgement, news_value, scripted_judge

ADMIN_URL = os.environ.get("AI_SIGNAL_IT_DATABASE_URL")
BOOTSTRAP = os.environ.get("AI_SIGNAL_IT_KAFKA_BOOTSTRAP_SERVERS")
SR_URL = os.environ.get("AI_SIGNAL_IT_SCHEMA_REGISTRY_URL")

pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and BOOTSTRAP and SR_URL),
    reason="AI_SIGNAL_IT_* environment variables are not set",
)

SERVICE_ROOT = Path(__file__).resolve().parents[1]


async def _admin(statement: str) -> None:
    assert ADMIN_URL is not None
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        _ = await connection.execute(sa.text(statement))
    await engine.dispose()


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    assert ADMIN_URL is not None
    name = f"ai_signal_doc_it_{uuid4().hex[:12]}"
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    url = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AI_SIGNAL_DATABASE_URL", url)
        config = Config(str(SERVICE_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
        command.upgrade(config, "head")
        try:
            yield url
        finally:
            asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _end_offsets(topic: str) -> list[TopicPartition]:
    assert BOOTSTRAP is not None
    probe = Consumer({"bootstrap.servers": BOOTSTRAP, "group.id": f"it-{uuid4().hex}"})
    try:
        partitions = sorted(
            probe.list_topics(topic, timeout=10).topics[topic].partitions
        )
        return [
            TopicPartition(
                topic,
                partition,
                probe.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=10
                )[1],
            )
            for partition in partitions
        ]
    finally:
        probe.close()


def _poll_one(consumer: Consumer, timeout_s: float = 20.0) -> Message:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        message = consumer.poll(1.0)
        if message is not None and message.error() is None:
            return message
    raise AssertionError("expected message was not consumed")


def _produce_news(
    serializer: AvroValueSerializer, value: dict[str, object]
) -> TopicPartition:
    assert BOOTSTRAP is not None
    reports: list[TopicPartition] = []

    def on_delivery(error: object, message: Message) -> None:
        assert error is None
        partition, offset = message.partition(), message.offset()
        assert partition is not None and offset is not None
        reports.append(TopicPartition(NEWS_RAW_TOPIC, partition, offset))

    key = news_idempotency_key(
        str(value["symbol"]), str(value["source_doc_id"]), str(value["title"])
    )
    producer = Producer({"bootstrap.servers": BOOTSTRAP})
    producer.produce(
        NEWS_RAW_TOPIC,
        value=serializer.serialize(NEWS_RAW_TOPIC, value),
        key=str(value["symbol"]).encode(),
        headers=[("idempotency_key", key)],
        on_delivery=on_delivery,
    )
    assert producer.flush(10) == 0
    (position,) = reports
    return position


def test_news_raw_to_judgement_outbox_and_signal_request(database_url: str) -> None:
    assert BOOTSTRAP is not None and SR_URL is not None
    client = schema_registry_client(SR_URL)
    serializer = AvroValueSerializer(client)
    factory = create_session_factory(
        create_async_engine(database_url, poolclass=NullPool)
    )
    source_doc_id = f"IT{uuid4().hex[:10]}"
    value = news_value(
        source_doc_id=source_doc_id, title=f"SK하이닉스 HBM 증설 {source_doc_id}"
    )
    judge, step = scripted_judge([judgement(direction=1, scope="SYMBOL")])

    position = _produce_news(serializer, value)
    group = f"it-doc-{uuid4().hex}"
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": group,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
        }
    )
    consumer.assign([position])
    message = _poll_one(consumer)
    loop = ConsumerLoop(
        ConsumerSpec(
            "document_processor",
            NEWS_RAW_TOPIC,
            DocumentProcessor(judge_factory=lambda: judge),
            group=group,
        ),
        consumer=consumer,
        deserializer=AvroValueDeserializer(client),
        serializer=serializer,
        session_factory=factory,
    )
    before = _end_offsets("signal-requests")
    try:
        asyncio.run(loop.process(message))
        asyncio.run(loop.process(message))
        (committed,) = consumer.committed(
            [TopicPartition(NEWS_RAW_TOPIC, position.partition)], timeout=10
        )
        assert committed.offset == (position.offset or 0) + 1
    finally:
        consumer.close()

    async def stored() -> tuple[object, ...]:
        async with factory() as session:
            row = (
                await session.execute(
                    sa.text(
                        "SELECT r.idempotency_key, d.version, e.link_method, "
                        "j.direction, j.prompt_version, "
                        "(SELECT count(*) FROM outbox.events) "
                        "FROM silver.document d "
                        "JOIN silver.document_kis_news k USING (document_id) "
                        "JOIN bronze.news_title_raw r ON r.raw_id = k.raw_id "
                        "JOIN silver.document_entity e USING (document_id) "
                        "JOIN silver.document_judgement j USING (document_id) "
                        "WHERE d.source_doc_id = :id"
                    ),
                    {"id": source_doc_id},
                )
            ).one()
            return tuple(row)

    key = news_idempotency_key("000660", source_doc_id, str(value["title"]))
    assert asyncio.run(stored()) == (
        key,
        1,
        "source_tag",
        1,
        "document_judgement/v1",
        1,
    )
    assert len(step.requests) == 1

    assert (
        asyncio.run(OutboxRelay(factory, create_producer(BOOTSTRAP)).relay_once()) == 1
    )
    reader = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP,
            "group.id": f"it-{uuid4().hex}",
            "enable.auto.commit": False,
        }
    )
    reader.assign(before)
    try:
        relayed = _poll_one(reader)
    finally:
        reader.close()
    assert relayed.key() == b"000660"
    payload = relayed.value()
    assert payload is not None
    assert AvroValueDeserializer(client).deserialize("signal-requests", payload) == {
        "symbol": "000660",
        "reason": "NEW_DOCUMENT",
    }

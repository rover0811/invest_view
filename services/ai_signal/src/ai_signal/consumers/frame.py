"""Common Kafka consumer frame for every ai_signal processor and sink (18.10).

A processor is one ``Handler`` plus one ``ConsumerSpec`` line in
``ai_signal.consumers.registry.CONSUMERS``. For each message the frame:

1. polls ``spec.topic`` with consumer group ``spec.group_id``
2. decodes the key as UTF-8 and the value with Schema Registry Avro
3. opens one DB transaction and calls the handler with the session and an
   ``OutboxWriter`` bound to that session, so data rows and outbox events
   commit together
4. commits the Kafka offset only after the DB transaction committed

Delivery is at least once: a crash after step 3 redelivers the message, so
handlers must be idempotent on business keys (doc 20). A failing handler is
retried ``max_attempts`` times, each in a fresh transaction; then the frame raises
without committing the offset and the process restarts, unless the spec has a
``dlq_topic``: then the original key, value bytes and headers are produced to it
with ``error.*`` headers (18.4) and the offset is committed. Messages that cannot
be decoded are logged and skipped (offset committed), as in ``tick_persistence``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, Protocol

from confluent_kafka import Consumer, KafkaError, Message, Producer, TopicPartition
from confluent_kafka.schema_registry.error import SchemaRegistryError
from confluent_kafka.serialization import SerializationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_signal.avro import (
    AvroValueDeserializer,
    AvroValueSerializer,
    schema_registry_client,
)
from ai_signal.config import AiSignalSettings
from ai_signal.db import create_engine, create_session_factory
from ai_signal.outbox.writer import OutboxWriter, ValueSerializer

logger = logging.getLogger(__name__)

GROUP_PREFIX = "ai-signal-"
DECODE_ERRORS = (SerializationError, SchemaRegistryError, ValueError, TypeError)


@dataclass(frozen=True)
class ConsumedMessage:
    topic: str
    partition: int
    offset: int
    key: str | None
    value: Mapping[str, Any]
    headers: Mapping[str, str]
    timestamp_ms: int | None


@dataclass(frozen=True)
class HandlerContext:
    session: AsyncSession
    outbox: OutboxWriter


Handler = Callable[[ConsumedMessage, HandlerContext], Awaitable[None]]


@dataclass(frozen=True)
class ConsumerSpec:
    name: str
    topic: str
    handler: Handler
    group: str | None = None
    dlq_topic: str | None = None

    @property
    def group_id(self) -> str:
        return self.group if self.group is not None else f"{GROUP_PREFIX}{self.name}"

    async def run(self, settings: AiSignalSettings) -> None:
        await run_consumer(self, settings)


class ConsumerHandlerError(RuntimeError):
    pass


class KafkaConsumerLike(Protocol):
    def subscribe(self, topics: list[str]) -> None: ...

    def poll(self, timeout: float) -> Message | None: ...

    def commit(
        self, *, offsets: list[TopicPartition], asynchronous: Literal[False]
    ) -> list[TopicPartition]: ...

    def close(self) -> None: ...


class DlqProducer(Protocol):
    def produce(
        self,
        topic: str,
        *,
        value: bytes | None = None,
        key: bytes | None = None,
        headers: list[tuple[str, bytes]] | None = None,
    ) -> None: ...

    def flush(self, timeout: float) -> int: ...


class ValueDeserializer(Protocol):
    def deserialize(self, topic: str, payload: bytes) -> dict[str, Any]: ...


def create_consumer(spec: ConsumerSpec, bootstrap_servers: str) -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": bootstrap_servers,
            "group.id": spec.group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "isolation.level": "read_committed",
        }
    )


def _text(raw: str | bytes | None) -> str | None:
    if raw is None:
        return None
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


def _headers(message: Message) -> dict[str, str]:
    raw = message.headers()
    if raw is None:
        return {}
    items = raw.items() if isinstance(raw, dict) else raw
    return {name: _text(value) or "" for name, value in items}


class ConsumerLoop:
    def __init__(
        self,
        spec: ConsumerSpec,
        *,
        consumer: KafkaConsumerLike,
        deserializer: ValueDeserializer,
        serializer: ValueSerializer,
        session_factory: async_sessionmaker[AsyncSession],
        poll_timeout: float = 1.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        dlq_producer: DlqProducer | None = None,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._spec = spec
        self._consumer = consumer
        self._deserializer = deserializer
        self._serializer = serializer
        self._session_factory = session_factory
        self._poll_timeout = poll_timeout
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._dlq_producer = dlq_producer
        if spec.dlq_topic is not None and dlq_producer is None:
            raise ValueError(f"{spec.name}: dlq_topic requires a dlq_producer")

    def decode(self, message: Message) -> ConsumedMessage | None:
        topic = message.topic() or self._spec.topic
        raw_value = message.value()
        if raw_value is None:
            logger.warning(
                "%s: skipping empty value offset=%s", self._spec.name, message.offset()
            )
            return None
        try:
            value = self._deserializer.deserialize(topic, raw_value)
        except DECODE_ERRORS as exc:
            logger.error(
                "%s: skipping undecodable message partition=%s offset=%s err=%s",
                self._spec.name,
                message.partition(),
                message.offset(),
                exc,
            )
            return None
        timestamp_type, timestamp_ms = message.timestamp()
        return ConsumedMessage(
            topic=topic,
            partition=message.partition() or 0,
            offset=message.offset() or 0,
            key=_text(message.key()),
            value=value,
            headers=_headers(message),
            timestamp_ms=timestamp_ms if timestamp_type != 0 else None,
        )

    async def handle(self, consumed: ConsumedMessage) -> None:
        """Run the handler in one transaction, retrying with a fresh transaction."""
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with self._session_factory() as session, session.begin():
                    context = HandlerContext(
                        session=session, outbox=OutboxWriter(session, self._serializer)
                    )
                    await self._spec.handler(consumed, context)
                return
            except Exception as exc:
                logger.warning(
                    "%s: handler failed attempt=%s/%s partition=%s offset=%s err=%s",
                    self._spec.name,
                    attempt,
                    self._max_attempts,
                    consumed.partition,
                    consumed.offset,
                    exc,
                )
                if attempt == self._max_attempts:
                    raise ConsumerHandlerError(
                        f"{self._spec.name}: handler failed at "
                        f"{consumed.topic}[{consumed.partition}]@{consumed.offset}"
                    ) from exc
                await asyncio.sleep(self._retry_backoff_seconds * 2 ** (attempt - 1))

    async def process(self, message: Message) -> None:
        consumed = self.decode(message)
        if consumed is not None:
            try:
                await self.handle(consumed)
            except ConsumerHandlerError as exc:
                if self._spec.dlq_topic is None:
                    raise
                await self._dead_letter(message, exc)
        await self._commit(message)

    async def _dead_letter(self, message: Message, exc: ConsumerHandlerError) -> None:
        producer = self._dlq_producer
        topic = self._spec.dlq_topic
        if producer is None or topic is None:
            raise exc
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        headers = [
            (name, value.encode("utf-8")) for name, value in _headers(message).items()
        ]
        headers += [
            ("error.consumer", self._spec.name.encode("utf-8")),
            ("error.class", type(cause).__name__.encode("utf-8")),
            ("error.message", str(cause)[:1000].encode("utf-8")),
            ("error.topic", (message.topic() or self._spec.topic).encode("utf-8")),
            ("error.partition", str(message.partition()).encode("utf-8")),
            ("error.offset", str(message.offset()).encode("utf-8")),
        ]
        raw_key = message.key()
        key = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
        raw_value = message.value()
        value = raw_value.encode("utf-8") if isinstance(raw_value, str) else raw_value
        producer.produce(topic, value=value, key=key, headers=headers)
        remaining = await asyncio.get_running_loop().run_in_executor(
            None, producer.flush, 10.0
        )
        if remaining:
            raise exc
        logger.error(
            "%s: sent %s[%s]@%s to %s: %s",
            self._spec.name,
            message.topic(),
            message.partition(),
            message.offset(),
            topic,
            cause,
        )

    async def _commit(self, message: Message) -> None:
        topic = message.topic() or self._spec.topic
        partition = message.partition()
        offset = message.offset()
        if partition is None or offset is None:
            logger.warning(
                "%s: cannot commit message without position", self._spec.name
            )
            return
        commit = partial(
            self._consumer.commit,
            offsets=[TopicPartition(topic, partition, offset + 1)],
            asynchronous=False,
        )
        _ = await asyncio.get_running_loop().run_in_executor(None, commit)

    async def poll(self) -> Message | None:
        message = await asyncio.get_running_loop().run_in_executor(
            None, self._consumer.poll, self._poll_timeout
        )
        if message is None:
            return None
        error = message.error()
        if error is None:
            return message
        if error.code() != KafkaError._PARTITION_EOF:
            logger.error("%s: kafka error %s", self._spec.name, error)
        return None

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop_event = stop if stop is not None else asyncio.Event()
        self._consumer.subscribe([self._spec.topic])
        logger.info(
            "%s: consuming topic=%s group=%s",
            self._spec.name,
            self._spec.topic,
            self._spec.group_id,
        )
        try:
            while not stop_event.is_set():
                message = await self.poll()
                if message is not None:
                    await self.process(message)
        finally:
            self._consumer.close()


async def run_consumer(spec: ConsumerSpec, settings: AiSignalSettings) -> None:
    engine = create_engine(settings.database_url)
    client = schema_registry_client(settings.schema_registry_url)
    loop = ConsumerLoop(
        spec,
        consumer=create_consumer(spec, settings.kafka_bootstrap_servers),
        deserializer=AvroValueDeserializer(client),
        serializer=AvroValueSerializer(client),
        session_factory=create_session_factory(engine),
        dlq_producer=(
            Producer(
                {
                    "bootstrap.servers": settings.kafka_bootstrap_servers,
                    "enable.idempotence": True,
                }
            )
            if spec.dlq_topic is not None
            else None
        ),
    )
    try:
        await loop.run()
    finally:
        await engine.dispose()

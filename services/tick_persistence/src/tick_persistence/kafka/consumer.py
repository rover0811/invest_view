"""Kafka consumer for stock-ticks topic with Confluent Schema Registry support.

Uses plain ``confluent_kafka.Consumer`` (NOT ``DeserializingConsumer``, which is
experimental per Confluent docs) with explicit ``AvroDeserializer`` calls.
Backpressure is enforced via a bounded ``asyncio.Queue`` between the synchronous
Kafka poll loop (run in a thread executor) and the async on_message dispatch.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from confluent_kafka import Consumer, KafkaError, KafkaException, Message
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext, SerializationError

from tick_persistence.config import TickPersistenceConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickMessage:
    value: dict[str, Any]
    topic: str
    partition: int
    offset: int
    headers: dict[str, str]


MessageHandler = Callable[[TickMessage], Awaitable[None]]


class _TaskLike(Protocol):
    def cancelled(self) -> bool: ...

    def exception(self) -> BaseException | None: ...

    def get_name(self) -> str: ...


class TickConsumer:
    def __init__(
        self,
        config: TickPersistenceConfig,
        on_message: MessageHandler,
        queue_maxsize: int = 1000,
        poll_timeout: float = 1.0,
    ) -> None:
        self._topic = config.kafka_topic
        self._on_message = on_message
        self._poll_timeout = poll_timeout

        self._sr_client = SchemaRegistryClient({"url": config.schema_registry_url})
        schema_str = Path(config.avro_schema_path).read_text()
        avro_deserializer = cast(Any, AvroDeserializer)
        self._deserializer = avro_deserializer(
            schema_registry_client=self._sr_client,
            schema_str=schema_str,
            from_dict=lambda obj, ctx: obj,
        )

        self._consumer = Consumer(
            {
                "bootstrap.servers": config.kafka_bootstrap_servers,
                "group.id": config.kafka_consumer_group,
                "auto.offset.reset": config.kafka_auto_offset_reset,
                "enable.auto.commit": False,
                "isolation.level": "read_committed",
            }
        )

        self._queue: asyncio.Queue[tuple[Message, dict[str, Any]]] = asyncio.Queue(maxsize=queue_maxsize)
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fatal_error: BaseException | None = None
        self._dead = asyncio.Event()

    async def start(self) -> None:
        self._consumer.subscribe([self._topic])
        self._loop = asyncio.get_running_loop()
        self._poll_task = asyncio.create_task(self._run_poll_loop(), name="tick-consumer-poll")
        self._dispatch_task = asyncio.create_task(self._run_dispatch_loop(), name="tick-consumer-dispatch")
        self._poll_task.add_done_callback(self._on_task_done)
        self._dispatch_task.add_done_callback(self._on_task_done)
        logger.info("tick consumer started topic=%s group=%s", self._topic, self._consumer.list_topics(timeout=2.0))

    def _on_task_done(self, task: _TaskLike) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        self._fatal_error = error
        logger.error("tick consumer task failed: %s", task.get_name(), exc_info=(type(error), error, error.__traceback__))
        self._dead.set()

    def is_alive(self) -> bool:
        return (
            self._fatal_error is None
            and self._poll_task is not None
            and self._dispatch_task is not None
            and not self._poll_task.done()
            and not self._dispatch_task.done()
        )

    async def wait_dead(self) -> None:
        await self._dead.wait()

    @property
    def fatal_error(self) -> BaseException | None:
        return self._fatal_error

    async def _run_poll_loop(self) -> None:
        assert self._loop is not None
        while not self._stop_event.is_set():
            try:
                msg = await self._loop.run_in_executor(None, self._consumer.poll, self._poll_timeout)
            except KafkaException as exc:
                logger.error("kafka poll exception: %s", exc)
                continue

            if msg is None:
                continue
            if msg.error() is not None:
                err = msg.error()
                if err is not None and err.code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("kafka message error: %s", err)
                continue

            try:
                raw_value = msg.value()
                if raw_value is None:
                    logger.warning("kafka tombstone or empty value, skipping; offset=%s", msg.offset())
                    self._consumer.commit(message=msg, asynchronous=False)
                    continue
                ctx = SerializationContext(msg.topic() or self._topic, MessageField.VALUE)
                dict_value = self._deserializer(raw_value, ctx)
            except SerializationError as exc:
                logger.error(
                    "avro deserialization failed; skipping offset=%s partition=%s err=%s",
                    msg.offset(),
                    msg.partition(),
                    exc,
                )
                self._consumer.commit(message=msg, asynchronous=False)
                continue
            except Exception as exc:
                logger.error("unexpected deserialization error: %s; skipping offset=%s", exc, msg.offset())
                self._consumer.commit(message=msg, asynchronous=False)
                continue

            if not isinstance(dict_value, dict):
                logger.error("deserializer returned non-dict (%s); skipping offset=%s", type(dict_value), msg.offset())
                self._consumer.commit(message=msg, asynchronous=False)
                continue

            await self._queue.put((msg, cast(dict[str, Any], dict_value)))

    async def _run_dispatch_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                msg, dict_value = await asyncio.wait_for(self._queue.get(), timeout=self._poll_timeout)
            except asyncio.TimeoutError:
                continue

            try:
                partition = msg.partition()
                offset = msg.offset()
                tick_message = TickMessage(
                    value=dict_value,
                    topic=msg.topic() or self._topic,
                    partition=partition if partition is not None else -1,
                    offset=offset if offset is not None else -1,
                    headers=_headers_to_dict(msg.headers()),
                )
                await self._on_message(tick_message)
                self._consumer.commit(message=msg, asynchronous=False)
            except Exception as exc:
                logger.error(
                    "on_message failed; NOT committing offset=%s err=%s; consumer will retry on next session",
                    msg.offset(),
                    exc,
                )
                raise

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._consumer.close()
        except Exception as exc:
            logger.warning("error closing consumer: %s", exc)


def _headers_to_dict(
    headers: list[tuple[str, bytes | str | None]] | dict[str, bytes | str | None] | None,
) -> dict[str, str]:
    if not headers:
        return {}
    result: dict[str, str] = {}
    items = headers.items() if isinstance(headers, dict) else headers
    for key, value in items:
        if value is None:
            result[key] = ""
        elif isinstance(value, bytes):
            result[key] = value.decode("utf-8")
        else:
            result[key] = value
    return result

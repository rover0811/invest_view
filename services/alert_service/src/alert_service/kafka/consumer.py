"""Kafka consumer for stock-alerts topic with Confluent Schema Registry support.

Uses plain ``confluent_kafka.Consumer`` (NOT ``DeserializingConsumer``, which is
experimental per Confluent docs) with explicit ``AvroDeserializer`` calls.
Backpressure is enforced via a bounded ``asyncio.Queue`` between the synchronous
Kafka poll loop (run in a thread executor) and the async on_message dispatch.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext, SerializationError

from alert_service.config import AlertServiceConfig

logger = logging.getLogger(__name__)


MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class AlertConsumer:
    def __init__(
        self,
        config: AlertServiceConfig,
        on_message: MessageHandler,
        queue_maxsize: int = 1000,
        poll_timeout: float = 1.0,
    ) -> None:
        self._topic = config.kafka_topic
        self._on_message = on_message
        self._poll_timeout = poll_timeout

        self._sr_client = SchemaRegistryClient({"url": config.schema_registry_url})
        schema_str = Path(config.avro_schema_path).read_text()
        self._deserializer = AvroDeserializer(
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

    async def start(self) -> None:
        self._consumer.subscribe([self._topic])
        self._loop = asyncio.get_running_loop()
        self._poll_task = asyncio.create_task(self._run_poll_loop(), name="alert-consumer-poll")
        self._dispatch_task = asyncio.create_task(self._run_dispatch_loop(), name="alert-consumer-dispatch")
        logger.info("alert consumer started topic=%s group=%s", self._topic, self._consumer.list_topics(timeout=2.0))

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
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("kafka message error: %s", err)
                continue

            try:
                raw_value = msg.value()
                if raw_value is None:
                    logger.warning("kafka tombstone or empty value, skipping; offset=%s", msg.offset())
                    self._consumer.commit(message=msg, asynchronous=False)
                    continue
                ctx = SerializationContext(msg.topic(), MessageField.VALUE)
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

            await self._queue.put((msg, dict_value))

    async def _run_dispatch_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                msg, dict_value = await asyncio.wait_for(self._queue.get(), timeout=self._poll_timeout)
            except asyncio.TimeoutError:
                continue

            try:
                await self._on_message(dict_value)
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

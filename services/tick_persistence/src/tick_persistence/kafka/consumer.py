"""Kafka consumer for stock-ticks topic with Confluent Schema Registry support.

Uses plain ``confluent_kafka.Consumer`` (NOT ``DeserializingConsumer``, which is
experimental per Confluent docs) with explicit ``AvroDeserializer`` calls.
Backpressure is enforced via a bounded ``asyncio.Queue`` between the synchronous
Kafka consume loop (run in a thread executor) and the async batch dispatch.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, TopicPartition
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer
from confluent_kafka.serialization import MessageField, SerializationContext, SerializationError

from tick_persistence.config import TickPersistenceConfig

if TYPE_CHECKING:
    from tick_persistence.observability.metrics import TickMetrics
    from tick_persistence.observability.reconciliation import ReconciliationLedger

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TickMessage:
    value: dict[str, Any]
    topic: str
    partition: int
    offset: int
    headers: dict[str, str]


MessageHandler = Callable[[TickMessage], Awaitable[None]]
BatchMessageHandler = Callable[[list[TickMessage]], Awaitable[None]]
StateClearer = Callable[[], None]
_QueuedMessage = tuple[Message, TickMessage | None]


class _TaskLike(Protocol):
    def cancelled(self) -> bool: ...

    def exception(self) -> BaseException | None: ...

    def get_name(self) -> str: ...


class TickConsumer:
    def __init__(
        self,
        config: TickPersistenceConfig,
        on_message: BatchMessageHandler,
        queue_maxsize: int = 1000,
        poll_timeout: float | None = None,
        batch_size: int | None = None,
        on_revoke: StateClearer | None = None,
        metrics: TickMetrics | None = None,
        ledger: ReconciliationLedger | None = None,
    ) -> None:
        self._topic = config.kafka_topic
        self._on_message = on_message
        self._poll_timeout = config.poll_timeout if poll_timeout is None else poll_timeout
        self._batch_size = config.batch_size if batch_size is None else batch_size
        self._on_revoke_clear_state = on_revoke
        self._metrics = metrics
        self._ledger = ledger

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
                "enable.auto.offset.store": False,
                "isolation.level": "read_committed",
                "max.poll.interval.ms": config.max_poll_interval_ms,
            }
        )

        self._queue: asyncio.Queue[list[_QueuedMessage]] = asyncio.Queue(maxsize=queue_maxsize)
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fatal_error: BaseException | None = None
        self._dead = asyncio.Event()
        self._last_successful_offsets: dict[tuple[str, int], TopicPartition] = {}

    async def start(self) -> None:
        self._consumer.subscribe([self._topic], on_assign=self._on_assign, on_revoke=self._on_revoke)
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
                messages = await self._loop.run_in_executor(
                    None,
                    partial(self._consumer.consume, num_messages=self._batch_size, timeout=self._poll_timeout),
                )
            except KafkaException as exc:
                logger.error("kafka consume exception: %s", exc)
                continue

            if self._metrics is not None:
                self._metrics.consume_batch_size.observe(len(messages))
                self._update_lag_metrics()

            if not messages:
                if self._metrics is not None:
                    self._metrics.empty_batches_total.inc()
                continue

            batch: list[_QueuedMessage] = []
            for msg in messages:
                queued = self._deserialize_message(msg)
                if queued is not None:
                    batch.append(queued)
            if not batch:
                continue
            await self._queue.put(batch)

    def _deserialize_message(self, msg: Message) -> _QueuedMessage | None:
        if msg.error() is not None:
            err = msg.error()
            if err is not None and err.code() == KafkaError._PARTITION_EOF:
                return None
            logger.error("kafka message error: %s", err)
            return None

        try:
            raw_value = msg.value()
            if raw_value is None:
                logger.warning("kafka tombstone or empty value, skipping; offset=%s", msg.offset())
                return (msg, None)
            ctx = SerializationContext(msg.topic() or self._topic, MessageField.VALUE)
            dict_value = self._deserializer(raw_value, ctx)
        except SerializationError as exc:
            logger.error(
                "avro deserialization failed; skipping offset=%s partition=%s err=%s",
                msg.offset(),
                msg.partition(),
                exc,
            )
            return (msg, None)
        except Exception as exc:
            logger.error("unexpected deserialization error: %s; skipping offset=%s", exc, msg.offset())
            return (msg, None)

        if not isinstance(dict_value, dict):
            logger.error("deserializer returned non-dict (%s); skipping offset=%s", type(dict_value), msg.offset())
            return (msg, None)

        partition = msg.partition()
        offset = msg.offset()
        tick_message = TickMessage(
            value=cast(dict[str, Any], dict_value),
            topic=msg.topic() or self._topic,
            partition=partition if partition is not None else -1,
            offset=offset if offset is not None else -1,
            headers=_headers_to_dict(msg.headers()),
        )
        return (msg, tick_message)

    async def _run_dispatch_loop(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                batch = await asyncio.wait_for(self._queue.get(), timeout=self._poll_timeout)
            except asyncio.TimeoutError:
                continue

            if self._metrics is not None:
                self._metrics.set_queue_depth(self._queue.qsize())

            try:
                tick_messages = [tick_message for _, tick_message in batch if tick_message is not None]
                if tick_messages:
                    try:
                        await self._on_message(tick_messages)
                    except Exception:
                        if self._metrics is not None:
                            self._metrics.handle_batch_failures_total.inc()
                        raise
                self._commit_batch_offsets(batch)
                self._record_reconciliation(batch)
            except Exception as exc:
                logger.error(
                    "batch handler failed; NOT committing batch size=%s err=%s; consumer will retry on next session",
                    len(batch),
                    exc,
                )
                raise

    def _commit_batch_offsets(self, batch: list[_QueuedMessage]) -> None:
        offsets_by_partition: dict[tuple[str, int], int] = {}
        for msg, _ in batch:
            topic = msg.topic() or self._topic
            partition = msg.partition()
            offset = msg.offset()
            if partition is None or offset is None:
                logger.warning("cannot commit message with missing partition/offset topic=%s", topic)
                continue
            key = (topic, partition)
            offsets_by_partition[key] = max(offset + 1, offsets_by_partition.get(key, offset + 1))
        offsets = [
            TopicPartition(topic, partition, offset)
            for (topic, partition), offset in sorted(offsets_by_partition.items())
        ]
        self._commit_topic_partitions(offsets, remember=True)

    def _commit_topic_partitions(self, offsets: list[TopicPartition], *, remember: bool) -> None:
        if not offsets:
            return
        start = time.perf_counter()
        try:
            self._consumer.commit(offsets=offsets, asynchronous=False)
        except Exception:
            if self._metrics is not None:
                self._metrics.commit_failures_total.inc()
            raise
        if self._metrics is not None:
            self._metrics.commit_duration_seconds.observe(time.perf_counter() - start)
            for offset in offsets:
                if offset.partition is not None and offset.offset is not None:
                    self._metrics.set_committed_offset(offset.partition, offset.offset)
        if not remember:
            return
        for offset in offsets:
            key = (offset.topic, offset.partition)
            current = self._last_successful_offsets.get(key)
            if current is None or offset.offset > current.offset:
                self._last_successful_offsets[key] = offset

    def _on_assign(self, consumer: Consumer, partitions: list[TopicPartition]) -> None:
        logger.info("kafka partitions assigned: %s", partitions)
        if self._metrics is not None:
            self._metrics.rebalance_total.labels(event="assign").inc()

    def _on_revoke(self, consumer: Consumer, partitions: list[TopicPartition]) -> None:
        logger.warning("kafka partitions revoked: %s", partitions)
        if self._metrics is not None:
            self._metrics.rebalance_total.labels(event="revoke").inc()
        self._discard_pending_batches()
        if self._on_revoke_clear_state is not None:
            self._on_revoke_clear_state()
        try:
            revoked = {(partition.topic, partition.partition) for partition in partitions}
            offsets = [
                offset
                for key, offset in self._last_successful_offsets.items()
                if not revoked or key in revoked
            ]
            self._commit_topic_partitions(offsets, remember=False)
        except Exception as exc:
            logger.error("failed to commit last successful offsets during revoke: %s", exc)

    def _discard_pending_batches(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _record_reconciliation(self, batch: list[_QueuedMessage]) -> None:
        if self._ledger is None:
            return
        for msg, tick_message in batch:
            partition = msg.partition()
            if partition is None:
                continue
            self._ledger.record_consumed(partition, 1)
            if tick_message is None:
                self._ledger.record_skip(partition, 1)
        self._ledger.verify()

    def _update_lag_metrics(self) -> None:
        if self._metrics is None:
            return
        try:
            assignment = self._consumer.assignment()
            if not assignment:
                return
            positions = {
                (tp.topic, tp.partition): tp.offset for tp in self._consumer.position(assignment)
            }
            for tp in assignment:
                position = positions.get((tp.topic, tp.partition))
                if position is None or position < 0:
                    continue
                try:
                    _low, high = self._consumer.get_watermark_offsets(tp, cached=True)
                except (KafkaException, RuntimeError):
                    continue
                if high is None or high < 0:
                    continue
                self._metrics.set_consumer_lag(tp.partition, max(0, high - position))
        except Exception as exc:
            logger.debug("lag metric update skipped: %s", exc)

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

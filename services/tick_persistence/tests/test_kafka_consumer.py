"""TickConsumer tests intentionally exercise private loops with mocks."""
# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportAny=false, reportUnknownArgumentType=false, reportUnusedParameter=false, reportMissingTypeStubs=false

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import KafkaError
from confluent_kafka.serialization import SerializationError

from tick_persistence.kafka.consumer import TickConsumer, TickMessage


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.kafka_topic = "stock-ticks"
    cfg.kafka_bootstrap_servers = "localhost:9092"
    cfg.kafka_consumer_group = "test-group"
    cfg.kafka_auto_offset_reset = "earliest"
    cfg.schema_registry_url = "http://localhost:8081"
    cfg.avro_schema_path = "/dev/null"
    return cfg


@pytest.fixture
def consumer_patches():
    with (
        patch("tick_persistence.kafka.consumer.Consumer") as MC,
        patch("tick_persistence.kafka.consumer.SchemaRegistryClient") as MS,
        patch("tick_persistence.kafka.consumer.AvroDeserializer") as MD,
        patch("tick_persistence.kafka.consumer.Path") as MP,
    ):
        MP.return_value.read_text.return_value = '{"type":"record","name":"X","fields":[]}'
        yield {"Consumer": MC, "SR": MS, "Avro": MD, "Path": MP}


def _make_msg(*, value=None, error=None, topic="stock-ticks", offset=42, partition=0, headers=None):
    msg = MagicMock()
    msg.value.return_value = value
    msg.error.return_value = error
    msg.topic.return_value = topic
    msg.offset.return_value = offset
    msg.partition.return_value = partition
    msg.headers.return_value = headers
    return msg


class _DoneTask:
    def __init__(self, exc: BaseException | None = None, cancelled: bool = False) -> None:
        self._exc = exc
        self._cancelled = cancelled

    def cancelled(self) -> bool:
        return self._cancelled

    def exception(self) -> BaseException | None:
        return self._exc

    def get_name(self) -> str:
        return "fake-consumer-task"


@pytest.mark.asyncio
async def test_consumer_supervision_records_task_exception(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer_patches["Consumer"].return_value.poll.return_value = None

    await consumer.start()
    assert consumer.is_alive()

    error = RuntimeError("dispatch died")
    consumer._on_task_done(_DoneTask(error))

    await asyncio.wait_for(consumer.wait_dead(), timeout=0.1)
    assert consumer.fatal_error is error
    assert not consumer.is_alive()

    consumer.stop()
    assert consumer._poll_task is not None
    assert consumer._dispatch_task is not None
    await asyncio.wait_for(consumer._poll_task, timeout=0.2)
    await asyncio.wait_for(consumer._dispatch_task, timeout=0.2)


@pytest.mark.asyncio
async def test_consumer_supervision_ignores_cancelled_task(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer_patches["Consumer"].return_value.poll.return_value = None

    await consumer.start()
    consumer._on_task_done(_DoneTask(RuntimeError("cancelled"), cancelled=True))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(consumer.wait_dead(), timeout=0.01)
    assert consumer.fatal_error is None
    assert consumer.is_alive()

    consumer.stop()
    assert consumer._poll_task is not None
    assert consumer._dispatch_task is not None
    await asyncio.wait_for(consumer._poll_task, timeout=0.2)
    await asyncio.wait_for(consumer._dispatch_task, timeout=0.2)


@pytest.mark.asyncio
async def test_start_subscribes_to_topic(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._stop_event.set()
    consumer_patches["Consumer"].return_value.poll.return_value = None

    await consumer.start()

    assert consumer._poll_task is not None
    assert consumer._dispatch_task is not None
    await consumer._poll_task
    await consumer._dispatch_task

    consumer_patches["Consumer"].return_value.subscribe.assert_called_once_with(["stock-ticks"])


@pytest.mark.asyncio
async def test_poll_handles_none_message(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    call_count = {"n": 0}

    def poll_se(timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
        return None

    consumer._consumer.poll.side_effect = poll_se

    await consumer._run_poll_loop()

    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.empty()
    assert call_count["n"] >= 1


@pytest.mark.asyncio
async def test_poll_handles_partition_eof(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    err = MagicMock()
    err.code.return_value = KafkaError._PARTITION_EOF
    msg = _make_msg(error=err)

    call_count = {"n": 0}

    def poll_se(timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
            return None
        return msg

    consumer._consumer.poll.side_effect = poll_se

    await consumer._run_poll_loop()

    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.empty()


@pytest.mark.asyncio
async def test_poll_commits_and_skips_on_serialization_error(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    msg = _make_msg(value=b"raw-bytes")
    consumer._deserializer.side_effect = SerializationError("bad payload")

    call_count = {"n": 0}
    def poll_se(timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
            return None
        return msg

    consumer._consumer.poll.side_effect = poll_se

    await consumer._run_poll_loop()

    consumer._consumer.commit.assert_called_once_with(message=msg, asynchronous=False)
    assert consumer._queue.empty()


@pytest.mark.asyncio
async def test_poll_deserializes_valid_message_then_dispatch_commits_after_handler(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    payload = {"symbol": "005930", "price": 70000}
    msg = _make_msg(
        value=b"raw-bytes",
        topic="stock-ticks",
        partition=2,
        offset=123,
        headers=[("trace-id", b"abc"), ("source", "kis")],
    )
    consumer._deserializer.return_value = payload

    call_count = {"n": 0}
    def poll_se(timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
            return None
        return msg

    consumer._consumer.poll.side_effect = poll_se
    await consumer._run_poll_loop()

    seen = []
    async def handler(tick_message: TickMessage) -> None:
        consumer._consumer.commit.assert_not_called()
        seen.append(tick_message)

    consumer._on_message = handler
    await consumer._run_dispatch_loop()

    assert seen == [
        TickMessage(
            value=payload,
            topic="stock-ticks",
            partition=2,
            offset=123,
            headers={"trace-id": "abc", "source": "kis"},
        )
    ]
    consumer._consumer.commit.assert_called_once_with(message=msg, asynchronous=False)


@pytest.mark.asyncio
async def test_dispatch_does_not_commit_when_handler_raises_and_dead_is_set(mock_config, consumer_patches):
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    consumer = TickConsumer(mock_config, handler, poll_timeout=0.01)

    msg = _make_msg()
    payload = {"symbol": "005930"}
    await consumer._queue.put((msg, payload))
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom") as exc_info:
        await consumer._run_dispatch_loop()
    consumer._on_task_done(_DoneTask(exc_info.value))

    handler.assert_awaited_once_with(TickMessage(value=payload, topic="stock-ticks", partition=0, offset=42, headers={}))
    consumer._consumer.commit.assert_not_called()
    await asyncio.wait_for(consumer.wait_dead(), timeout=0.1)
    assert consumer.fatal_error is exc_info.value


@pytest.mark.asyncio
async def test_dispatch_fail_fast_does_not_advance_to_later_message(mock_config, consumer_patches):
    handler = AsyncMock(side_effect=[RuntimeError("boom"), None])
    consumer = TickConsumer(mock_config, handler, poll_timeout=0.01)

    msg1 = _make_msg(offset=10)
    msg2 = _make_msg(offset=20)
    payload1 = {"symbol": "005930"}
    payload2 = {"symbol": "000660"}
    await consumer._queue.put((msg1, payload1))
    await consumer._queue.put((msg2, payload2))
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom"):
        await consumer._run_dispatch_loop()

    handler.assert_awaited_once_with(TickMessage(value=payload1, topic="stock-ticks", partition=0, offset=10, headers={}))
    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.get_nowait() == (msg2, payload2)


def test_stop_sets_event_and_closes(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)

    assert not consumer._stop_event.is_set()
    consumer.stop()

    assert consumer._stop_event.is_set()
    consumer._consumer.close.assert_called_once()

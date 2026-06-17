"""TickConsumer tests intentionally exercise private loops with mocks."""
# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownMemberType=false, reportAny=false, reportUnknownArgumentType=false, reportUnusedParameter=false, reportMissingTypeStubs=false

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import KafkaError, TopicPartition
from confluent_kafka.serialization import SerializationError

from tick_persistence.kafka.consumer import TickConsumer, TickMessage


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.kafka_topic = "stock-ticks"
    cfg.kafka_bootstrap_servers = "localhost:9092"
    cfg.kafka_consumer_group = "test-group"
    cfg.kafka_auto_offset_reset = "earliest"
    cfg.batch_size = 500
    cfg.max_queued_messages = 5000
    cfg.poll_timeout = 1.0
    cfg.max_poll_interval_ms = 300_000
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


async def _wait_until(predicate, *, timeout: float = 0.2) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.001)


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


def _offset_triples(offsets: list[TopicPartition]) -> list[tuple[str, int, int]]:
    return sorted((tp.topic, tp.partition, tp.offset) for tp in offsets)


@pytest.mark.asyncio
async def test_consumer_supervision_records_task_exception(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer_patches["Consumer"].return_value.consume.return_value = []

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
    consumer_patches["Consumer"].return_value.consume.return_value = []

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
    consumer_patches["Consumer"].return_value.consume.return_value = []

    await consumer.start()

    assert consumer._poll_task is not None
    assert consumer._dispatch_task is not None
    await consumer._poll_task
    await consumer._dispatch_task

    _, kwargs = consumer_patches["Consumer"].return_value.subscribe.call_args
    assert consumer_patches["Consumer"].return_value.subscribe.call_args.args == (["stock-ticks"],)
    assert callable(kwargs["on_assign"])
    assert callable(kwargs["on_revoke"])


@pytest.mark.asyncio
async def test_poll_handles_none_message(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    call_count = {"n": 0}

    def consume_se(*, num_messages, timeout):
        assert num_messages == 500
        assert timeout == 0.01
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
        return []

    consumer._consumer.consume.side_effect = consume_se

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

    def consume_se(*, num_messages, timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
            return []
        return [msg]

    consumer._consumer.consume.side_effect = consume_se

    await consumer._run_poll_loop()

    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.empty()


@pytest.mark.asyncio
async def test_deserialize_error_is_skipped_but_offset_is_committed_after_batch_success(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    bad_msg = _make_msg(value=b"bad-bytes", offset=10, partition=0)
    good_msg = _make_msg(value=b"good-bytes", offset=11, partition=0)
    payload = {"symbol": "005930", "price": 70000}
    consumer._deserializer.side_effect = [SerializationError("bad payload"), payload]

    call_count = {"n": 0}
    def consume_se(*, num_messages, timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
            return []
        return [bad_msg, good_msg]

    consumer._consumer.consume.side_effect = consume_se

    await consumer._run_poll_loop()

    seen = []

    async def handler(batch: list[TickMessage]) -> None:
        consumer._consumer.commit.assert_not_called()
        seen.extend(batch)

    consumer._on_message = handler
    await consumer._run_dispatch_loop()

    assert seen == [TickMessage(value=payload, topic="stock-ticks", partition=0, offset=11, headers={})]
    consumer._consumer.commit.assert_called_once()
    assert _offset_triples(consumer._consumer.commit.call_args.kwargs["offsets"]) == [("stock-ticks", 0, 12)]
    assert consumer._consumer.commit.call_args.kwargs["asynchronous"] is False


@pytest.mark.asyncio
async def test_batch_dispatch_commits_partition_offsets_after_handler_success(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    payload1 = {"symbol": "005930", "price": 70000}
    payload2 = {"symbol": "000660", "price": 120000}
    payload3 = {"symbol": "035420", "price": 200000}
    msg1 = _make_msg(
        value=b"raw-bytes",
        topic="stock-ticks",
        partition=2,
        offset=123,
        headers=[("trace-id", b"abc"), ("source", "kis")],
    )
    msg2 = _make_msg(value=b"raw-bytes", topic="stock-ticks", partition=1, offset=7)
    msg3 = _make_msg(value=b"raw-bytes", topic="stock-ticks", partition=2, offset=125)
    consumer._deserializer.side_effect = [payload1, payload2, payload3]

    call_count = {"n": 0}
    def consume_se(*, num_messages, timeout):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            consumer._stop_event.set()
            return []
        return [msg1, msg2, msg3]

    consumer._consumer.consume.side_effect = consume_se
    await consumer._run_poll_loop()

    seen = []

    async def handler(batch: list[TickMessage]) -> None:
        consumer._consumer.commit.assert_not_called()
        seen.extend(batch)

    consumer._on_message = handler
    await consumer._run_dispatch_loop()

    assert seen == [
        TickMessage(
            value=payload1,
            topic="stock-ticks",
            partition=2,
            offset=123,
            headers={"trace-id": "abc", "source": "kis"},
        ),
        TickMessage(value=payload2, topic="stock-ticks", partition=1, offset=7, headers={}),
        TickMessage(value=payload3, topic="stock-ticks", partition=2, offset=125, headers={}),
    ]
    consumer._consumer.commit.assert_called_once()
    assert _offset_triples(consumer._consumer.commit.call_args.kwargs["offsets"]) == [
        ("stock-ticks", 1, 8),
        ("stock-ticks", 2, 126),
    ]
    assert consumer._consumer.commit.call_args.kwargs["asynchronous"] is False


@pytest.mark.asyncio
async def test_dispatch_does_not_commit_when_handler_raises_and_dead_is_set(mock_config, consumer_patches):
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    consumer = TickConsumer(mock_config, handler, poll_timeout=0.01)

    msg = _make_msg()
    payload = {"symbol": "005930"}
    await consumer._queue.put([(msg, TickMessage(value=payload, topic="stock-ticks", partition=0, offset=42, headers={}))])
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom") as exc_info:
        await consumer._run_dispatch_loop()
    consumer._on_task_done(_DoneTask(exc_info.value))

    handler.assert_awaited_once_with([TickMessage(value=payload, topic="stock-ticks", partition=0, offset=42, headers={})])
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
    batch1 = [(msg1, TickMessage(value=payload1, topic="stock-ticks", partition=0, offset=10, headers={}))]
    batch2 = [(msg2, TickMessage(value=payload2, topic="stock-ticks", partition=0, offset=20, headers={}))]
    await consumer._queue.put(batch1)
    await consumer._queue.put(batch2)
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom"):
        await consumer._run_dispatch_loop()

    handler.assert_awaited_once_with([TickMessage(value=payload1, topic="stock-ticks", partition=0, offset=10, headers={})])
    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.get_nowait() == batch2


@pytest.mark.asyncio
async def test_empty_consume_batch_continues_without_commit(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._loop = asyncio.get_running_loop()

    calls = {"n": 0}

    def consume_se(*, num_messages, timeout):
        calls["n"] += 1
        if calls["n"] >= 2:
            consumer._stop_event.set()
        return []

    consumer._consumer.consume.side_effect = consume_se

    await consumer._run_poll_loop()

    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.empty()


@pytest.mark.asyncio
async def test_poll_loop_blocks_when_queued_message_budget_is_full(mock_config, consumer_patches):
    mock_config.batch_size = 2
    mock_config.max_queued_messages = 4
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    loop = asyncio.get_running_loop()
    consumer._loop = loop

    batches = [
        [_make_msg(value=b"raw", offset=0), _make_msg(value=b"raw", offset=1)],
        [_make_msg(value=b"raw", offset=2), _make_msg(value=b"raw", offset=3)],
        [_make_msg(value=b"raw", offset=4), _make_msg(value=b"raw", offset=5)],
    ]
    consumer._deserializer.side_effect = [
        {"symbol": "005930", "seq": seq} for seq in range(6)
    ]
    third_batch_consumed = asyncio.Event()
    calls = {"n": 0}

    def consume_se(*, num_messages, timeout):
        assert num_messages == 2
        calls["n"] += 1
        if calls["n"] == 3:
            loop.call_soon_threadsafe(third_batch_consumed.set)
        if calls["n"] <= len(batches):
            return batches[calls["n"] - 1]
        return []

    consumer._consumer.consume.side_effect = consume_se
    task = asyncio.create_task(consumer._run_poll_loop())

    await asyncio.wait_for(third_batch_consumed.wait(), timeout=0.2)
    await _wait_until(lambda: consumer._queue.qsize() >= 2)
    await asyncio.sleep(0.02)

    assert consumer._queue.qsize() == 2

    consumer.stop()
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_revoke_discards_pending_batches_clears_state_and_commits_success_offsets(mock_config, consumer_patches):
    clear_state = MagicMock()
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01, on_revoke=clear_state)
    msg = _make_msg(offset=10)
    consumer._queue.put_nowait([(msg, TickMessage(value={"symbol": "005930"}, topic="stock-ticks", partition=0, offset=10, headers={}))])
    consumer._last_successful_offsets[("stock-ticks", 0)] = TopicPartition("stock-ticks", 0, 9)

    consumer._on_revoke(consumer._consumer, [TopicPartition("stock-ticks", 0)])

    assert consumer._queue.empty()
    clear_state.assert_called_once_with()
    consumer._consumer.commit.assert_called_once()
    assert _offset_triples(consumer._consumer.commit.call_args.kwargs["offsets"]) == [("stock-ticks", 0, 9)]
    assert consumer._consumer.commit.call_args.kwargs["asynchronous"] is False


def test_stop_sets_event_and_closes(mock_config, consumer_patches):
    consumer = TickConsumer(mock_config, AsyncMock(), poll_timeout=0.01)

    assert not consumer._stop_event.is_set()
    consumer.stop()

    assert consumer._stop_event.is_set()
    consumer._consumer.close.assert_called_once()

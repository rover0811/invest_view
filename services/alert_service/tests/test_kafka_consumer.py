import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import KafkaError
from confluent_kafka.serialization import SerializationError

from alert_service.kafka.consumer import AlertConsumer


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.kafka_topic = "stock-alerts"
    cfg.kafka_bootstrap_servers = "localhost:9092"
    cfg.kafka_consumer_group = "test-group"
    cfg.kafka_auto_offset_reset = "earliest"
    cfg.schema_registry_url = "http://localhost:8081"
    cfg.avro_schema_path = "/dev/null"
    return cfg


@pytest.fixture
def consumer_patches():
    with (
        patch("alert_service.kafka.consumer.Consumer") as MC,
        patch("alert_service.kafka.consumer.SchemaRegistryClient") as MS,
        patch("alert_service.kafka.consumer.AvroDeserializer") as MD,
        patch("alert_service.kafka.consumer.Path") as MP,
    ):
        MP.return_value.read_text.return_value = '{"type":"record","name":"X","fields":[]}'
        yield {"Consumer": MC, "SR": MS, "Avro": MD, "Path": MP}


def _make_msg(*, value=None, error=None, topic="stock-alerts", offset=42, partition=0):
    msg = MagicMock()
    msg.value.return_value = value
    msg.error.return_value = error
    msg.topic.return_value = topic
    msg.offset.return_value = offset
    msg.partition.return_value = partition
    return msg


@pytest.mark.asyncio
async def test_start_subscribes_to_topic(mock_config, consumer_patches):
    consumer = AlertConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._stop_event.set()
    consumer_patches["Consumer"].return_value.poll.return_value = None

    await consumer.start()

    assert consumer._poll_task is not None
    assert consumer._dispatch_task is not None
    await consumer._poll_task
    await consumer._dispatch_task

    consumer_patches["Consumer"].return_value.subscribe.assert_called_once_with(["stock-alerts"])


@pytest.mark.asyncio
async def test_poll_handles_none_message(mock_config, consumer_patches):
    consumer = AlertConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
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
    consumer = AlertConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
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
    consumer = AlertConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
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
async def test_dispatch_calls_on_message_and_commits(mock_config, consumer_patches):
    handler = AsyncMock()
    consumer = AlertConsumer(mock_config, handler, poll_timeout=0.01)

    msg = _make_msg()
    payload = {"alert_id": "a1", "symbol": "005930"}
    await consumer._queue.put((msg, payload))
    consumer._stop_event.set()

    await consumer._run_dispatch_loop()

    handler.assert_awaited_once_with(payload)
    consumer._consumer.commit.assert_called_once_with(message=msg, asynchronous=False)


@pytest.mark.asyncio
async def test_dispatch_does_not_commit_when_handler_raises(mock_config, consumer_patches):
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    consumer = AlertConsumer(mock_config, handler, poll_timeout=0.01)

    msg = _make_msg()
    payload = {"alert_id": "a1"}
    await consumer._queue.put((msg, payload))
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom"):
        await consumer._run_dispatch_loop()

    handler.assert_awaited_once_with(payload)
    consumer._consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_fail_fast_does_not_advance_to_later_message(mock_config, consumer_patches):
    handler = AsyncMock(side_effect=[RuntimeError("boom"), None])
    consumer = AlertConsumer(mock_config, handler, poll_timeout=0.01)

    msg1 = _make_msg(offset=10)
    msg2 = _make_msg(offset=20)
    payload1 = {"alert_id": "a1"}
    payload2 = {"alert_id": "a2"}
    await consumer._queue.put((msg1, payload1))
    await consumer._queue.put((msg2, payload2))
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom"):
        await consumer._run_dispatch_loop()

    handler.assert_awaited_once_with(payload1)
    consumer._consumer.commit.assert_not_called()
    assert consumer._queue.get_nowait() == (msg2, payload2)


def test_stop_sets_event_and_closes(mock_config, consumer_patches):
    consumer = AlertConsumer(mock_config, AsyncMock(), poll_timeout=0.01)

    assert not consumer._stop_event.is_set()
    consumer.stop()

    assert consumer._stop_event.is_set()
    consumer._consumer.close.assert_called_once()

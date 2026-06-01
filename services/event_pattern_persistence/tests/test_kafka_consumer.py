from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from event_pattern_persistence.handler import make_pattern_handler
from event_pattern_persistence.kafka.consumer import PatternConsumer


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.kafka_topic = "stock-patterns"
    cfg.kafka_bootstrap_servers = "localhost:9092"
    cfg.kafka_consumer_group = "test-group"
    cfg.kafka_auto_offset_reset = "earliest"
    cfg.schema_registry_url = "http://localhost:8081"
    cfg.avro_schema_path = "/dev/null"
    return cfg


@pytest.fixture
def consumer_patches():
    with (
        patch("event_pattern_persistence.kafka.consumer.Consumer") as MC,
        patch("event_pattern_persistence.kafka.consumer.SchemaRegistryClient") as MS,
        patch("event_pattern_persistence.kafka.consumer.AvroDeserializer") as MD,
        patch("event_pattern_persistence.kafka.consumer.Path") as MP,
    ):
        MP.return_value.read_text.return_value = '{"type":"record","name":"X","fields":[]}'
        yield {"Consumer": MC, "SR": MS, "Avro": MD, "Path": MP}


def _make_msg(*, value=None, error=None, topic="stock-patterns", offset=7, partition=0):
    msg = MagicMock()
    msg.value.return_value = value
    msg.error.return_value = error
    msg.topic.return_value = topic
    msg.offset.return_value = offset
    msg.partition.return_value = partition
    return msg


async def test_make_pattern_handler_forwards_to_repo_insert():
    repo = AsyncMock()
    handler = make_pattern_handler(repo)
    payload = {"pattern_event_id": "p1", "symbol": "005930", "pattern_type": "GOLDEN_CROSS"}
    await handler(payload)
    repo.insert.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_start_subscribes_to_stock_patterns(mock_config, consumer_patches):
    consumer = PatternConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    consumer._stop_event.set()
    consumer_patches["Consumer"].return_value.poll.return_value = None

    await consumer.start()

    assert consumer._poll_task is not None
    assert consumer._dispatch_task is not None
    await consumer._poll_task
    await consumer._dispatch_task
    consumer_patches["Consumer"].return_value.subscribe.assert_called_once_with(["stock-patterns"])


@pytest.mark.asyncio
async def test_dispatch_calls_on_message_then_commits(mock_config, consumer_patches):
    handler = AsyncMock()
    consumer = PatternConsumer(mock_config, handler, poll_timeout=0.01)

    msg = _make_msg()
    payload = {"pattern_event_id": "p1", "symbol": "005930"}
    await consumer._queue.put((msg, payload))
    consumer._stop_event.set()

    await consumer._run_dispatch_loop()

    handler.assert_awaited_once_with(payload)
    consumer._consumer.commit.assert_called_once_with(message=msg, asynchronous=False)


@pytest.mark.asyncio
async def test_dispatch_does_not_commit_when_handler_raises(mock_config, consumer_patches):
    handler = AsyncMock(side_effect=RuntimeError("boom"))
    consumer = PatternConsumer(mock_config, handler, poll_timeout=0.01)

    msg = _make_msg()
    await consumer._queue.put((msg, {"pattern_event_id": "p1"}))
    consumer._stop_event.set()

    with pytest.raises(RuntimeError, match="boom"):
        await consumer._run_dispatch_loop()

    consumer._consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_valid_pattern_dict_reaches_repo_insert_and_commits(mock_config, consumer_patches):
    repo = AsyncMock()
    consumer = PatternConsumer(mock_config, make_pattern_handler(repo), poll_timeout=0.01)

    msg = _make_msg()
    payload = {"pattern_event_id": "p1", "symbol": "005930", "pattern_type": "GOLDEN_CROSS"}
    await consumer._queue.put((msg, payload))
    consumer._stop_event.set()

    await consumer._run_dispatch_loop()

    repo.insert.assert_awaited_once_with(payload)
    consumer._consumer.commit.assert_called_once_with(message=msg, asynchronous=False)


def test_stop_sets_event_and_closes(mock_config, consumer_patches):
    consumer = PatternConsumer(mock_config, AsyncMock(), poll_timeout=0.01)
    assert not consumer._stop_event.is_set()
    consumer.stop()
    assert consumer._stop_event.is_set()
    consumer._consumer.close.assert_called_once()

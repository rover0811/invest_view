import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


from kis_ingestion.producer import StockTickProducer
from kis_ingestion.tick_parser import KISTickParser, ParsedTick

SCHEMA_PATH = str(Path(__file__).resolve().parents[3] / "schemas" / "stock-ticks.avsc")
SR_URL = "http://localhost:8081"


@pytest.fixture
def sample_tick() -> ParsedTick:
    raw_values = [
        "005930", "123929", "73100", "2", "100", "0.14", "73050.5", "73000", "73500", "72900",
        "73200", "73100", "1000", "500000", "3650000000", "20000", "30000", "-10000", "95.5", "100000",
        "120000", "1", "0.45", "1.2", "090000", "2", "100", "103000", "2", "500",
        "091500", "5", "-200", "20260501", "1", "0", "5000", "6000", "100000", "120000",
        "0.05", "450000", "1.1", "1", "0", "73100"
    ]
    parser = KISTickParser()
    return parser.parse(
        raw_record_values=raw_values,
        source_tr_id="H0STCNT0",
        market="KRX",
        received_at="2026-05-01T12:39:29Z"
    )


@pytest.fixture
def mock_kafka_deps():
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory, \
         patch("kis_ingestion.producer.SchemaRegistryClient") as mock_sr_factory, \
         patch("kis_ingestion.producer.AvroSerializer") as mock_serializer_cls:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        mock_sr_client = MagicMock()
        mock_sr_factory.return_value = mock_sr_client
        mock_serializer = MagicMock()
        mock_serializer.return_value = b"\x00\x00\x00\x00\x01serialized-bytes"
        mock_serializer_cls.return_value = mock_serializer
        yield {
            "producer_factory": mock_producer_factory,
            "producer": mock_producer,
            "sr_factory": mock_sr_factory,
            "sr_client": mock_sr_client,
            "serializer_cls": mock_serializer_cls,
            "serializer": mock_serializer,
        }


def test_publish_serializes_and_produces(sample_tick, mock_kafka_deps):
    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)
    producer.publish(sample_tick, "session-1", 42)

    mock_producer = mock_kafka_deps["producer"]
    mock_producer.produce.assert_called_once()
    call_args = mock_producer.produce.call_args.kwargs
    assert call_args["topic"] == "stock-ticks"
    assert call_args["key"] == b"005930"
    assert call_args["value"] == b"\x00\x00\x00\x00\x01serialized-bytes"

    headers = dict(call_args["headers"])
    assert headers["session_id"] == b"session-1"
    assert headers["sequence"] == b"42"
    assert call_args["on_delivery"] == producer._on_delivery

    producer_config = mock_kafka_deps["producer_factory"].call_args.args[0]
    assert producer_config["bootstrap.servers"] == "localhost:9092"
    assert producer_config["acks"] == "all"
    assert producer_config["enable.idempotence"] is True

    serializer_kwargs = mock_kafka_deps["serializer_cls"].call_args.kwargs
    assert serializer_kwargs.get("conf", {}).get("auto.register.schemas") is False

    mock_kafka_deps["sr_factory"].assert_called_once_with({"url": SR_URL})


def test_publish_avro_roundtrip(sample_tick, mock_kafka_deps):
    from confluent_kafka.serialization import MessageField

    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)
    producer.publish(sample_tick, "session-1", 42)

    mock_serializer = mock_kafka_deps["serializer"]
    mock_serializer.assert_called_once()
    serializer_call = mock_serializer.call_args
    passed_dict = serializer_call.args[0]
    assert passed_dict["symbol"] == "005930"
    assert passed_dict["price"] == 73100

    ctx = serializer_call.args[1]
    assert ctx.topic == "stock-ticks"
    assert ctx.field == MessageField.VALUE


def test_publish_calls_poll(sample_tick, mock_kafka_deps):
    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)
    producer.publish(sample_tick, "session-1", 42)

    mock_kafka_deps["producer"].poll.assert_called_once_with(0)


def test_publish_handles_buffer_error(sample_tick, mock_kafka_deps):
    mock_kafka_deps["producer"].produce.side_effect = BufferError("Queue full")

    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)
    producer.publish(sample_tick, "session-1", 42)

    mock_kafka_deps["producer"].produce.assert_called_once()


def test_flush_delegates_to_producer(mock_kafka_deps):
    mock_kafka_deps["producer"].flush.return_value = 0

    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)
    result = producer.flush(30.0)

    assert result == 0
    mock_kafka_deps["producer"].flush.assert_called_once_with(30.0)


def test_on_delivery_logs_error(mock_kafka_deps):
    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)

    mock_msg = MagicMock()
    mock_msg.topic.return_value = "stock-ticks"
    mock_msg.partition.return_value = 0

    with patch("kis_ingestion.producer.logger") as mock_logger:
        producer._on_delivery(err="SomeError", msg=mock_msg)
        mock_logger.error.assert_called_once()
        assert "Kafka delivery failed" in mock_logger.error.call_args.args[0]


def test_on_delivery_logs_debug_on_success(mock_kafka_deps):
    producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH, SR_URL)

    mock_msg = MagicMock()
    mock_msg.topic.return_value = "stock-ticks"
    mock_msg.partition.return_value = 0
    mock_msg.offset.return_value = 100

    with patch("kis_ingestion.producer.logger") as mock_logger:
        producer._on_delivery(err=None, msg=mock_msg)
        mock_logger.debug.assert_called_once()
        assert "Kafka delivery ok" in mock_logger.debug.call_args.args[0]

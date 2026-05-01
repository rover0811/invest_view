import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from io import BytesIO
from decimal import Decimal

import pytest
import fastavro

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


from kis_ingestion.producer import StockTickProducer
from kis_ingestion.tick_parser import KISTickParser, ParsedTick

SCHEMA_PATH = str(Path(__file__).resolve().parents[3] / "schemas" / "stock-ticks.avsc")

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

def test_publish_serializes_and_produces(sample_tick):
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        producer.publish(sample_tick, "session-1", 42)
        
        mock_producer.produce.assert_called_once()
        call_args = mock_producer.produce.call_args.kwargs
        assert call_args["topic"] == "stock-ticks"
        assert call_args["key"] == b"005930"
        assert isinstance(call_args["value"], bytes)
        assert len(call_args["value"]) > 0
        
        headers = dict(call_args["headers"])
        assert headers["session_id"] == b"session-1"
        assert headers["sequence"] == b"42"
        assert call_args["on_delivery"] == producer._on_delivery

def test_publish_avro_roundtrip(sample_tick):
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        producer.publish(sample_tick, "session-1", 42)
        
        value = mock_producer.produce.call_args.kwargs["value"]
        
        schema = fastavro.schema.load_schema(SCHEMA_PATH)
        
        with BytesIO(value) as bio:
            deserialized = fastavro.schemaless_reader(bio, schema)
            
        assert deserialized["symbol"] == "005930"
        assert deserialized["price"] == 73100
        assert isinstance(deserialized["change_rate"], Decimal)
        assert deserialized["change_rate"] == Decimal("0.14")

def test_publish_calls_poll(sample_tick):
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        producer.publish(sample_tick, "session-1", 42)
        
        mock_producer.poll.assert_called_once_with(0)

def test_publish_handles_buffer_error(sample_tick):
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        mock_producer.produce.side_effect = BufferError("Queue full")
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        
        producer.publish(sample_tick, "session-1", 42)
        
        mock_producer.produce.assert_called_once()

def test_flush_delegates_to_producer():
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        mock_producer.flush.return_value = 0
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        result = producer.flush(30.0)
        
        assert result == 0
        mock_producer.flush.assert_called_once_with(30.0)

def test_on_delivery_logs_error():
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        
        mock_msg = MagicMock()
        mock_msg.topic.return_value = "stock-ticks"
        mock_msg.partition.return_value = 0
        
        with patch("kis_ingestion.producer.logger") as mock_logger:
            producer._on_delivery(err="SomeError", msg=mock_msg)
            mock_logger.error.assert_called_once()
            assert "Kafka delivery failed" in mock_logger.error.call_args.args[0]

def test_on_delivery_logs_debug_on_success():
    with patch("kis_ingestion.producer.Producer") as mock_producer_factory:
        mock_producer = MagicMock()
        mock_producer_factory.return_value = mock_producer
        
        producer = StockTickProducer("localhost:9092", "stock-ticks", SCHEMA_PATH)
        
        mock_msg = MagicMock()
        mock_msg.topic.return_value = "stock-ticks"
        mock_msg.partition.return_value = 0
        mock_msg.offset.return_value = 100
        
        with patch("kis_ingestion.producer.logger") as mock_logger:
            producer._on_delivery(err=None, msg=mock_msg)
            mock_logger.debug.assert_called_once()
            assert "Kafka delivery ok" in mock_logger.debug.call_args.args[0]

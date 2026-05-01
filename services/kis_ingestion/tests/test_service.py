import asyncio
import pytest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path so we can import kis_ingestion
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kis_ingestion.service import IngestionService


def make_mock_config():
    return SimpleNamespace(
        watch_symbols=["005930"],
        kafka_enabled=True,
        kafka_bootstrap_servers="localhost:9092",
        kafka_topic="stock-ticks",
    )


def make_mock_tick(symbol="005930", market="KRX", price=73100):
    return SimpleNamespace(
        symbol=symbol,
        market=market,
        price=price,
        market_session_code="0",
    )


class MockAsyncIterator:
    def __init__(self, ticks):
        self._ticks = list(ticks)
        self._index = 0
        self.connect = AsyncMock()
        self.stop = AsyncMock()
    
    def __aiter__(self):
        return self
    
    async def __anext__(self):
        if self._index >= len(self._ticks):
            raise StopAsyncIteration
        tick = self._ticks[self._index]
        self._index += 1
        return tick


@pytest.mark.asyncio
async def test_service_run_consumes_iterator_and_publishes():
    tick1 = make_mock_tick(symbol="005930", price=73100)
    tick2 = make_mock_tick(symbol="035720", price=52000)
    
    ticks = [
        (tick1, "session-1", 1),
        (tick2, "session-1", 2),
    ]
    
    mock_cm = MockAsyncIterator(ticks)
    mock_producer = MagicMock()
    mock_producer.flush = MagicMock(return_value=0)
    mock_http = AsyncMock()
    mock_http.aclose = AsyncMock()
    config = make_mock_config()
    
    service = IngestionService(
        config=config,
        connection_manager=mock_cm,
        http_client=mock_http,
        producer=mock_producer,
    )
    
    await service.run()
    
    mock_cm.connect.assert_awaited_once()
    assert mock_producer.publish.call_count == 2
    mock_producer.publish.assert_any_call(tick1, "session-1", 1)
    mock_producer.publish.assert_any_call(tick2, "session-1", 2)


@pytest.mark.asyncio
async def test_service_run_handles_publish_error_gracefully():
    tick1 = make_mock_tick(symbol="005930", price=73100)
    tick2 = make_mock_tick(symbol="035720", price=52000)
    
    ticks = [
        (tick1, "session-1", 1),
        (tick2, "session-1", 2),
    ]
    
    mock_cm = MockAsyncIterator(ticks)
    mock_producer = MagicMock()
    mock_producer.flush = MagicMock(return_value=0)
    mock_producer.publish.side_effect = [RuntimeError("Kafka down"), None]
    mock_http = AsyncMock()
    mock_http.aclose = AsyncMock()
    config = make_mock_config()
    
    service = IngestionService(
        config=config,
        connection_manager=mock_cm,
        http_client=mock_http,
        producer=mock_producer,
    )
    
    with patch("kis_ingestion.service.logger") as mock_logger:
        await service.run()
        
        assert mock_producer.publish.call_count == 2
        mock_logger.exception.assert_called()

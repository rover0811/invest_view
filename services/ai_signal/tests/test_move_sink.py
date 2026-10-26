from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from ai_signal.consumers.registry import CONSUMERS
from ai_signal.moves.sink import PRICE_MOVES_TOPIC, MoveRecord, MoveSink


def test_record_uses_kst_business_date() -> None:
    record = MoveRecord.from_value(
        {
            "move_id": "6f1c1d2e-0000-5000-8000-000000000001",
            "move_type": "DAILY",
            "symbol": "005930",
            "window_start": datetime(2026, 3, 27, 6, 30, tzinfo=UTC),
            "window_end": datetime(2026, 3, 30, 6, 30, tzinfo=UTC),
            "return_pct": 0.0125,
            "price_state": "POSITIVE",
            "signal_status": "NO_CANDIDATE",
            "version": 3,
        }
    )
    assert record.business_date == date(2026, 3, 30)
    assert record.return_pct == Decimal("0.0125")
    assert record.version == 3


def test_move_sink_registered_once() -> None:
    specs = [s for s in CONSUMERS if s.topic == PRICE_MOVES_TOPIC]
    assert len(specs) == 1
    assert specs[0].group_id == "ai-signal-move_sink"
    assert isinstance(specs[0].handler, MoveSink)

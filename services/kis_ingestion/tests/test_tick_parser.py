from decimal import Decimal
import pytest
from kis_ingestion.tick_parser import KISTickParser, ParsedTick


def test_parse_tick_success():
    # 46 fields matching TickRecord._FIELD_ORDER
    raw_values = [
        "005930", "123929", "73100", "2", "100", "0.14", "73050.5", "73000", "73500", "72900",
        "73200", "73100", "1000", "500000", "3650000000", "20000", "30000", "-10000", "95.5", "100000",
        "120000", "1", "0.45", "1.2", "090000", "2", "100", "103000", "2", "500",
        "091500", "5", "-200", "20260501", "1", "0", "5000", "6000", "100000", "120000",
        "0.05", "450000", "1.1", "1", "0", "73100"
    ]
    
    parser = KISTickParser()
    parsed = parser.parse(
        raw_record_values=raw_values,
        source_tr_id="H0STCNT0",
        market="KRX",
        received_at="2026-05-01T12:39:29Z"
    )
    
    assert isinstance(parsed, ParsedTick)
    assert parsed.symbol == "005930"
    assert parsed.price == 73100
    assert parsed.change_rate == Decimal("0.14")
    assert parsed.vwap == Decimal("73050.5")
    assert parsed.source_tr_id == "H0STCNT0"
    assert parsed.market == "KRX"
    assert parsed.received_at == "2026-05-01T12:39:29Z"


def test_parse_tick_invalid_count():
    parser = KISTickParser()
    with pytest.raises(ValueError, match="Expected 46 fields"):
        parser.parse(["only", "one"], "H0STCNT0", "KRX", "now")


def test_parsed_tick_field_count():
    # 46 tick fields + 3 meta fields = 49 fields
    assert len(ParsedTick.model_fields) == 49

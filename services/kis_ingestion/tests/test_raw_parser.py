import json
import pytest
from kis_ingestion.raw_parser import KISRawMessageParser, RawKISMessage


def test_parse_data_message():
    raw = "0|H0STCNT0|001|payload"
    parsed = KISRawMessageParser.parse(raw)
    assert isinstance(parsed, RawKISMessage)
    assert parsed.encrypted is False
    assert parsed.tr_id == "H0STCNT0"
    assert parsed.count == 1
    assert parsed.payload == "payload"


def test_parse_encrypted_message():
    raw = "1|H0STCNT0|001|payload"
    parsed = KISRawMessageParser.parse(raw)
    assert parsed.encrypted is True


def test_parse_invalid_message():
    assert KISRawMessageParser.parse("") is None
    assert KISRawMessageParser.parse("invalid") is None
    assert KISRawMessageParser.parse("2|H0STCNT0|001|payload") is None


def test_is_pingpong():
    pingpong = json.dumps({"header": {"tr_id": "PINGPONG"}})
    not_pingpong = json.dumps({"header": {"tr_id": "H0STCNT0"}})
    
    assert KISRawMessageParser.is_pingpong(pingpong) is True
    assert KISRawMessageParser.is_pingpong(not_pingpong) is False
    assert KISRawMessageParser.is_pingpong("invalid json") is False


def test_is_json_response():
    ack = json.dumps({"header": {"tr_id": "H0STCNT0"}, "body": {"rt_cd": "0"}})
    pingpong = json.dumps({"header": {"tr_id": "PINGPONG"}})
    
    assert KISRawMessageParser.is_json_response(ack) is True
    assert KISRawMessageParser.is_json_response(pingpong) is False
    assert KISRawMessageParser.is_json_response("invalid json") is False


def test_parse_json_response():
    data = {"header": {"tr_id": "H0STCNT0"}}
    raw = json.dumps(data)
    assert KISRawMessageParser.parse_json_response(raw) == data


def test_split_records():
    payload = "val1^val2^val3"
    assert KISRawMessageParser.split_records(payload) == ["val1", "val2", "val3"]

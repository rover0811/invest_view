import json
from dataclasses import dataclass
from typing import Any


@dataclass
class RawKISMessage:
    encrypted: bool      # True if first char is "1"
    tr_id: str           # e.g. "H0STCNT0"
    count: int           # number of records
    payload: str         # raw payload string


class KISRawMessageParser:
    @staticmethod
    def parse(raw: str) -> RawKISMessage | None:
        """
        Parse KIS WebSocket data message envelope.
        Format: encrypted|tr_id|count|payload
        Example: 0|H0STCNT0|001|payload...
        """
        if not raw or raw[0] not in ("0", "1"):
            return None

        parts = raw.split("|", 3)
        if len(parts) < 4:
            return None

        try:
            return RawKISMessage(
                encrypted=parts[0] == "1",
                tr_id=parts[1],
                count=int(parts[2]),
                payload=parts[3]
            )
        except (ValueError, IndexError):
            return None

    @staticmethod
    def is_pingpong(raw: str) -> bool:
        """Check if message is a PINGPONG message."""
        try:
            data: dict[str, Any] = json.loads(raw)
            return data.get("header", {}).get("tr_id") == "PINGPONG"
        except (json.JSONDecodeError, AttributeError):
            return False

    @staticmethod
    def is_json_response(raw: str) -> bool:
        """Check if message is a JSON response (subscribe ACK/NACK) and NOT pingpong."""
        try:
            data: dict[str, Any] = json.loads(raw)
            return data.get("header", {}).get("tr_id") != "PINGPONG"
        except (json.JSONDecodeError, AttributeError):
            return False

    @staticmethod
    def parse_json_response(raw: str) -> dict[str, Any]:
        """Parse JSON response and return the dict."""
        return json.loads(raw)

    @staticmethod
    def split_records(payload: str) -> list[str]:
        """
        Split payload by "^" into individual field values.
        Note: records in the payload are separated by "^" between ALL fields across ALL records.
        """
        return payload.split("^")

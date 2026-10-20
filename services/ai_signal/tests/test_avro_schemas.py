from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, cast

parse_schema = importlib.import_module("fastavro").parse_schema

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = REPO_ROOT / "schemas"
EXPECTED_FILES = {
    "news-raw.avsc",
    "flow-raw.avsc",
    "opinion-raw.avsc",
    "price-moves.avsc",
    "signal-requests.avsc",
    "signal-updates.avsc",
}


def _load(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")),
    )


def _field(schema: dict[str, Any], name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        next(field for field in schema["fields"] if field["name"] == name),
    )


def _enum_symbols(field: dict[str, Any]) -> list[str]:
    field_type = field["type"]
    if isinstance(field_type, list):
        field_type = next(item for item in field_type if isinstance(item, dict))
    return cast(list[str], field_type["symbols"])


def test_new_schemas_parse_and_use_shared_namespace() -> None:
    assert {path.name for path in SCHEMA_DIR.glob("*.avsc")} >= EXPECTED_FILES
    for filename in EXPECTED_FILES:
        schema = _load(filename)
        assert schema["namespace"] == "com.invest_view.events"
        parse_schema(schema)

    assert _field(_load("news-raw.avsc"), "source_payload")["type"] == {
        "type": "map",
        "values": "string",
    }


def test_fixed_topic_enums_and_nullable_decomposition_fields() -> None:
    requests = _load("signal-requests.avsc")
    assert _enum_symbols(_field(requests, "reason")) == [
        "PRICE_STATE_CHANGE",
        "NEW_DOCUMENT",
        "FLOW_UPDATE",
        "TARGET_PRICE_UPDATE",
        "MARKET_CLOSE",
    ]

    moves = _load("price-moves.avsc")
    assert _enum_symbols(_field(moves, "move_type")) == ["DAILY", "INTRADAY_SPIKE"]
    for name in (
        "contrib_global",
        "contrib_market",
        "contrib_sector",
        "contrib_idio",
        "beta_method",
    ):
        field = _field(moves, name)
        assert field["type"][0] == "null"
        assert field["default"] is None

    updates = _load("signal-updates.avsc")
    assert _enum_symbols(_field(updates, "generation_method")) == [
        "llm",
        "flow_rule",
        "decomposition",
    ]


def test_news_raw_carries_idempotency_key_as_required_string() -> None:
    field = _field(_load("news-raw.avsc"), "idempotency_key")
    assert field["type"] == "string"
    assert "default" not in field


def test_signal_updates_carry_event_time_ordering_key() -> None:
    updates = _load("signal-updates.avsc")
    assert _field(updates, "move_version")["type"] == "int"
    assert _field(updates, "move_window_end")["type"] == {
        "type": "long",
        "logicalType": "timestamp-millis",
    }
    for name in ("move_version", "move_window_end"):
        assert "default" not in _field(updates, name)
    assert "processing time" in _field(updates, "updated_at")["doc"].lower()
    assert "(move_version, version)" in updates["doc"]


def test_signal_updates_carry_symbol_name_and_cause_document_ids() -> None:
    updates = _load("signal-updates.avsc")
    symbol_name = _field(updates, "symbol_name")
    assert symbol_name["type"] == "string"
    assert "default" not in symbol_name
    cause_fields = {
        field["name"]: field
        for field in _field(updates, "causes")["type"]["items"]["fields"]
    }
    ids = cause_fields["source_document_ids"]
    assert ids["type"] == {"type": "array", "items": "string"}
    assert "default" not in ids
    assert "source_count" in cause_fields


def test_registration_map_includes_all_new_subjects_with_dlq_reuse() -> None:
    script_path = REPO_ROOT / "scripts" / "register_schemas.py"
    spec = importlib.util.spec_from_file_location("register_schemas", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    expected = {
        "news-raw-value": "schemas/news-raw.avsc",
        "flow-raw-value": "schemas/flow-raw.avsc",
        "opinion-raw-value": "schemas/opinion-raw.avsc",
        "price-moves-value": "schemas/price-moves.avsc",
        "signal-requests-value": "schemas/signal-requests.avsc",
        "signal-updates-value": "schemas/signal-updates.avsc",
        "signal-dlq-value": "schemas/signal-requests.avsc",
    }
    assert module.SCHEMA_SUBJECTS.items() >= expected.items()
    assert module.COMPATIBILITY == "BACKWARD"
    assert all(
        (module.REPO_ROOT / path).is_file() for path in module.SCHEMA_SUBJECTS.values()
    )

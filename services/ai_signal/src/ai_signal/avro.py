"""Schema Registry backed Avro serialization shared by every ai_signal component.

Schemas live in the repo ``schemas/<topic>.avsc`` (copied to ``/app/schemas`` in
the image). Subjects follow the Confluent TopicNameStrategy (``<topic>-value``)
and are registered by ``scripts/register_schemas.py``. Serializers never
auto-register: a payload that does not match the registered contract fails.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any, cast

from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroDeserializer, AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

# Topics whose value schema file is not ``<topic>.avsc`` (18.4).
SCHEMA_FILE_OVERRIDES: Mapping[str, str] = {"signal-dlq": "signal-requests.avsc"}


class SchemaNotFoundError(LookupError):
    pass


@cache
def default_schema_dir() -> Path:
    """Find the repo/image ``schemas`` directory by walking up from this file."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "schemas"
        if candidate.is_dir() and any(candidate.glob("*.avsc")):
            return candidate
    raise SchemaNotFoundError("schemas directory not found above ai_signal package")


def schema_path(topic: str, schema_dir: Path | None = None) -> Path:
    directory = schema_dir if schema_dir is not None else default_schema_dir()
    path = directory / SCHEMA_FILE_OVERRIDES.get(topic, f"{topic}.avsc")
    if not path.is_file():
        raise SchemaNotFoundError(f"no Avro schema file for topic {topic}: {path}")
    return path


def schema_str(topic: str, schema_dir: Path | None = None) -> str:
    return schema_path(topic, schema_dir).read_text(encoding="utf-8").strip()


def record_name(topic: str, schema_dir: Path | None = None) -> str:
    """Avro record name of the topic value schema, e.g. ``SignalRequest``."""
    parsed = cast(dict[str, Any], json.loads(schema_str(topic, schema_dir)))
    return str(parsed["name"])


def schema_registry_client(url: str) -> SchemaRegistryClient:
    return SchemaRegistryClient({"url": url})


def _record_to_dict(obj: object, _ctx: SerializationContext) -> dict[str, object]:
    if not isinstance(obj, dict):
        raise TypeError("Avro value must be a dict record")
    return cast(dict[str, object], obj)


def _dict_to_record(obj: dict[str, object], _ctx: SerializationContext) -> object:
    return obj


ToDict = Callable[[object, SerializationContext], dict[str, object]]
FromDict = Callable[[dict[str, object], SerializationContext], object]
# AvroSerializer/AvroDeserializer bind ``__init__`` to a name-mangled method, so
# type checkers cannot see their constructor signature. These aliases restate it.
_new_serializer = cast(
    Callable[[SchemaRegistryClient, str, ToDict, dict[str, object]], AvroSerializer],
    AvroSerializer,
)
_new_deserializer = cast(
    Callable[[SchemaRegistryClient, str, FromDict], AvroDeserializer],
    AvroDeserializer,
)


class AvroValueSerializer:
    """Serialize topic values to Confluent wire format (magic byte + schema id)."""

    def __init__(
        self, client: SchemaRegistryClient, schema_dir: Path | None = None
    ) -> None:
        self._client = client
        self._schema_dir = schema_dir
        self._serializers: dict[str, AvroSerializer] = {}
        self._record_names: dict[str, str] = {}

    def serialize(self, topic: str, value: Mapping[str, object]) -> bytes:
        serializer = self._serializers.get(topic)
        if serializer is None:
            serializer = _new_serializer(
                self._client,
                schema_str(topic, self._schema_dir),
                _record_to_dict,
                {"auto.register.schemas": False},
            )
            self._serializers[topic] = serializer
        payload = serializer(
            dict(value), SerializationContext(topic, MessageField.VALUE)
        )
        if payload is None:
            raise ValueError(f"Avro serializer returned no bytes for topic {topic}")
        return payload

    def record_name(self, topic: str) -> str:
        name = self._record_names.get(topic)
        if name is None:
            name = record_name(topic, self._schema_dir)
            self._record_names[topic] = name
        return name


class AvroValueDeserializer:
    """Decode Confluent wire format values with the repo schema as reader schema."""

    def __init__(
        self, client: SchemaRegistryClient, schema_dir: Path | None = None
    ) -> None:
        self._client = client
        self._schema_dir = schema_dir
        self._deserializers: dict[str, AvroDeserializer] = {}

    def deserialize(self, topic: str, payload: bytes) -> dict[str, Any]:
        deserializer = self._deserializers.get(topic)
        if deserializer is None:
            deserializer = _new_deserializer(
                self._client,
                schema_str(topic, self._schema_dir),
                _dict_to_record,
            )
            self._deserializers[topic] = deserializer
        value = deserializer(payload, SerializationContext(topic, MessageField.VALUE))
        if not isinstance(value, dict):
            raise TypeError(f"Avro value for topic {topic} is not a record")
        return cast(dict[str, Any], value)

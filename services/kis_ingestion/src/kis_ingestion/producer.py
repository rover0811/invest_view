import json
import logging
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from .event_id import compute_event_id
from .tick_parser import ParsedTick


logger = logging.getLogger(__name__)


class KafkaMessage(Protocol):
    def topic(self) -> str: ...

    def partition(self) -> int: ...

    def offset(self) -> int: ...


DeliveryCallback = Callable[[object | None, KafkaMessage], None]


class ProducerLike(Protocol):
    def produce(
        self,
        *,
        topic: str,
        key: bytes,
        value: bytes,
        headers: Sequence[tuple[str, bytes]],
        on_delivery: DeliveryCallback,
    ) -> None: ...

    def poll(self, timeout: float) -> int: ...

    def flush(self, timeout: float) -> int: ...

    def __len__(self) -> int: ...


ProducerFactory = Callable[[dict[str, object]], ProducerLike]


class SchemaRegistryClientLike(Protocol):
    pass


class SchemaRegistryClientFactory(Protocol):
    def __call__(self, conf: dict[str, str]) -> SchemaRegistryClientLike: ...


class AvroSerializerCallable(Protocol):
    def __call__(self, value: dict[str, object], ctx: object) -> bytes: ...


class AvroSerializerFactory(Protocol):
    def __call__(
        self,
        schema_registry_client: SchemaRegistryClientLike,
        schema_str: str,
        conf: dict[str, object] | None = None,
    ) -> AvroSerializerCallable: ...


class SerializationContextFactory(Protocol):
    def __call__(self, topic: str, field: object) -> object: ...


class MessageFieldType(Protocol):
    VALUE: object


Producer = cast(
    ProducerFactory,
    cast(object, getattr(import_module("confluent_kafka"), "Producer")),
)
SchemaRegistryClient = cast(
    SchemaRegistryClientFactory,
    cast(object, getattr(import_module("confluent_kafka.schema_registry"), "SchemaRegistryClient")),
)
AvroSerializer = cast(
    AvroSerializerFactory,
    cast(object, getattr(import_module("confluent_kafka.schema_registry.avro"), "AvroSerializer")),
)
SerializationContext = cast(
    SerializationContextFactory,
    cast(object, getattr(import_module("confluent_kafka.serialization"), "SerializationContext")),
)
MessageField = cast(
    MessageFieldType,
    cast(object, getattr(import_module("confluent_kafka.serialization"), "MessageField")),
)


class StockTickProducer:
    """Publishes ParsedTick as Avro-serialized messages to Kafka stock-ticks topic."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        schema_path: str,
        schema_registry_url: str,
    ) -> None:
        self._topic: str = topic
        self._sr_client: SchemaRegistryClientLike = SchemaRegistryClient({"url": schema_registry_url})
        schema_str = Path(schema_path).read_text()
        self._schema_has_event_id: bool = _schema_has_field(schema_str, "event_id")
        self._value_serializer: AvroSerializerCallable = AvroSerializer(
            self._sr_client,
            schema_str,
            conf={"auto.register.schemas": False},
        )
        self._producer: ProducerLike = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
        })

    def publish(self, tick: ParsedTick, session_id: str, sequence: int) -> None:
        tick_dict = cast(dict[str, object], tick.model_dump())
        event_id = compute_event_id(tick_dict)
        if self._schema_has_event_id:
            tick_dict["event_id"] = event_id
        try:
            value_bytes = self._value_serializer(
                tick_dict,
                SerializationContext(self._topic, MessageField.VALUE),
            )
            self._producer.produce(
                topic=self._topic,
                key=tick.symbol.encode("utf-8"),
                value=value_bytes,
                headers=[
                    ("session_id", session_id.encode("utf-8")),
                    ("sequence", str(sequence).encode("utf-8")),
                    # Compatibility seam until T10 adds event_id to stock-ticks Avro:
                    # publish the deterministic id out-of-band without sending an
                    # extra Avro field that the current registered schema rejects.
                    ("event_id", event_id.encode("utf-8")),
                ],
                on_delivery=self._on_delivery,
            )
            _ = self._producer.poll(0)
        except BufferError:
            logger.error("Kafka producer queue full, dropping tick symbol=%s", tick.symbol)

    def _on_delivery(self, err: object | None, msg: KafkaMessage) -> None:
        if err:
            logger.error(
                "Kafka delivery failed: %s [topic=%s partition=%s]",
                err,
                msg.topic(),
                msg.partition(),
            )
            return

        logger.debug(
            "Kafka delivery ok: topic=%s partition=%s offset=%s",
            msg.topic(),
            msg.partition(),
            msg.offset(),
        )

    def flush(self, timeout: float = 30.0) -> int:
        return self._producer.flush(timeout)

    def __len__(self) -> int:
        return len(self._producer)


def _schema_has_field(schema_str: str, field_name: str) -> bool:
    schema: object = json.loads(schema_str)  # pyright: ignore[reportAny]
    if not isinstance(schema, dict):
        return False
    schema_dict = cast(dict[str, object], schema)
    fields = schema_dict.get("fields")
    if not isinstance(fields, list):
        return False
    field_values = cast(list[object], fields)

    for field in field_values:
        if isinstance(field, dict) and cast(dict[str, object], field).get("name") == field_name:
            return True
    return False

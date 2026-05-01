import logging
from collections.abc import Callable, Sequence
from importlib import import_module
from io import BytesIO
from typing import Protocol, cast

from .tick_parser import ParsedTick


logger = logging.getLogger(__name__)


class FastAvroSchemaModule(Protocol):
    def load_schema(self, path: str) -> object: ...


class FastAvroModule(Protocol):
    schema: FastAvroSchemaModule

    def schemaless_writer(
        self,
        fo: BytesIO,
        schema: object,
        record: dict[str, object],
    ) -> None: ...


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


ProducerFactory = Callable[[dict[str, str]], ProducerLike]

fastavro = cast(FastAvroModule, cast(object, import_module("fastavro")))
fastavro_schema = cast(FastAvroSchemaModule, cast(object, import_module("fastavro.schema")))
Producer = cast(ProducerFactory, cast(object, getattr(import_module("confluent_kafka"), "Producer")))


class StockTickProducer:
    """Publishes ParsedTick as Avro-serialized messages to Kafka stock-ticks topic."""

    def __init__(self, bootstrap_servers: str, topic: str, schema_path: str) -> None:
        self._producer: ProducerLike = Producer({"bootstrap.servers": bootstrap_servers})
        self._topic: str = topic
        self._schema: object = fastavro_schema.load_schema(schema_path)

    def publish(self, tick: ParsedTick, session_id: str, sequence: int) -> None:
        tick_dict = cast(dict[str, object], tick.model_dump())
        buffer = BytesIO()
        fastavro.schemaless_writer(buffer, self._schema, tick_dict)
        avro_bytes = buffer.getvalue()

        try:
            self._producer.produce(
                topic=self._topic,
                key=tick.symbol.encode("utf-8"),
                value=avro_bytes,
                headers=[
                    ("session_id", session_id.encode("utf-8")),
                    ("sequence", str(sequence).encode("utf-8")),
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

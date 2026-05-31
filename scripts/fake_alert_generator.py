"""
Fake alert generator — produces synthetic StockAlert → Kafka (Avro via Schema Registry).

Mirrors scripts/fake_tick_generator.py but targets the stock-alerts topic. The
alert_service has no producer abstraction, so this uses confluent_kafka's
AvroSerializer directly against the registered ``stock-alerts-value`` subject.

Usage (run inside the alert_service project env, which ships confluent_kafka):
    uv run --project services/alert_service python scripts/fake_alert_generator.py \
        --alert-event-id 11111111-1111-1111-1111-111111111111 \
        --symbol 005930 --rule-name qa-synthetic

Requires: docker-compose.dev.yml Kafka on localhost:9092 and Schema Registry on
localhost:8081 (the stock-alerts-value subject must already be registered, which
it is because Flink produces and alert_service consumes that topic).

Synthetic-only: defaults to rule_name=qa-synthetic. Never use real rule_names.
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from uuid import uuid4

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "stock-alerts.avsc"

MARKETS = ("KRX", "NXT")
ALERT_TYPES = ("PRICE_ALERT", "VI_IMMINENT", "MOMENTUM_SHIFT", "TRADING_HALT")
SEVERITIES = ("INFO", "WARNING", "CRITICAL")


def build_alert(
    *,
    alert_event_id: str,
    symbol: str,
    market: str,
    alert_type: str,
    severity: str,
    rule_name: str,
) -> dict[str, object]:
    """Build a dict matching every field of schemas/stock-alerts.avsc.

    timestamp-millis fields are plain int milliseconds since epoch; fastavro
    (used by confluent's AvroSerializer) passes ints through for that logical type.
    """
    now_ms = int(time.time() * 1000)
    return {
        "alert_event_id": alert_event_id,
        "symbol": symbol,
        "market": market,
        "alert_type": alert_type,
        "severity": severity,
        "observation_start_at": now_ms - 60_000,
        "observation_end_at": now_ms,
        "triggered_at": now_ms,
        "trigger_values": {"current_price": "72000"},
        "source_tick_event_id": None,
        "rule_name": rule_name,
    }


def _alert_to_dict(alert: dict[str, object], ctx: SerializationContext) -> dict[str, object]:
    _ = ctx
    return alert


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake StockAlert generator for Kafka E2E testing")
    parser.add_argument("--alert-event-id", default=None, help="UUID for the alert (default: random uuid4)")
    parser.add_argument("--symbol", default="005930", help="Stock symbol code")
    parser.add_argument("--rule-name", default="qa-synthetic", help="Rule name (synthetic only)")
    parser.add_argument("--alert-type", default="PRICE_ALERT", choices=ALERT_TYPES, help="Alert type enum")
    parser.add_argument("--severity", default="WARNING", choices=SEVERITIES, help="Severity enum")
    parser.add_argument("--market", default="KRX", choices=MARKETS, help="Market enum")
    parser.add_argument("--count", type=int, default=1, help="Number of alerts to produce")
    parser.add_argument("--broker", default="localhost:9092", help="Kafka bootstrap servers")
    parser.add_argument("--schema-registry", default="http://localhost:8081", help="Schema Registry URL")
    parser.add_argument("--topic", default="stock-alerts", help="Kafka topic")
    args = parser.parse_args()

    schema_str = SCHEMA_PATH.read_text(encoding="utf-8")

    sr_client = SchemaRegistryClient({"url": args.schema_registry})
    # auto.register=False + use.latest.version=True: never create/modify SR subjects;
    # serialize against the already-registered stock-alerts-value schema.
    avro_serializer = AvroSerializer(
        sr_client,
        schema_str,
        _alert_to_dict,
        conf={"auto.register.schemas": False, "use.latest.version": True},
    )
    producer = Producer({
        "bootstrap.servers": args.broker,
        "acks": "all",
        "enable.idempotence": True,
    })
    ctx = SerializationContext(args.topic, MessageField.VALUE)

    delivery_errors: list[str] = []

    def _on_delivery(err: object | None, msg: object) -> None:
        if err is not None:
            delivery_errors.append(str(err))
            logger.error("Kafka delivery failed: %s", err)

    logger.info(
        "Producing %d alert(s): symbol=%s rule=%s type=%s severity=%s market=%s -> %s @ %s (SR=%s)",
        args.count, args.symbol, args.rule_name, args.alert_type, args.severity,
        args.market, args.topic, args.broker, args.schema_registry,
    )

    produced_ids: list[str] = []
    for i in range(max(1, args.count)):
        if i == 0 and args.alert_event_id:
            alert_event_id = args.alert_event_id
        else:
            alert_event_id = str(uuid4())

        alert = build_alert(
            alert_event_id=alert_event_id,
            symbol=args.symbol,
            market=args.market,
            alert_type=args.alert_type,
            severity=args.severity,
            rule_name=args.rule_name,
        )
        serialized = avro_serializer(alert, ctx)
        producer.produce(topic=args.topic, key=alert_event_id, value=serialized, on_delivery=_on_delivery)
        producer.poll(0)
        produced_ids.append(alert_event_id)
        logger.info("Produced alert_event_id=%s", alert_event_id)

    remaining = producer.flush(10)
    if remaining > 0:
        logger.error("Flush incomplete: %d message(s) still queued", remaining)
        sys.exit(1)
    if delivery_errors:
        logger.error("Delivery failed for %d message(s): %s", len(delivery_errors), delivery_errors)
        sys.exit(1)

    # Machine-greppable summary line for the verification harness.
    for aid in produced_ids:
        print(f"PRODUCED_ALERT_EVENT_ID={aid}")
    logger.info("Done: %d alert(s) produced and flushed.", len(produced_ids))


if __name__ == "__main__":
    main()

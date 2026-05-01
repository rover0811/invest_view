"""
Kafka consumer that reads stock-ticks, deserializes Avro, and prints to stdout.

Usage:
    uv run --project services/kis_ingestion python scripts/verify_kafka_consumer.py \
        --broker localhost:9092 --topic stock-ticks --timeout 30
"""

import argparse
import logging
import sys
import time
from decimal import Decimal
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "kis_ingestion" / "src"))

from confluent_kafka import Consumer, KafkaError
from fastavro import schemaless_reader
from fastavro.schema import load_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_PATH = str(Path(__file__).resolve().parent.parent / "schemas" / "stock-ticks.avsc")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify stock-ticks Kafka messages")
    parser.add_argument("--broker", default="localhost:9092")
    parser.add_argument("--topic", default="stock-ticks")
    parser.add_argument("--timeout", type=int, default=30, help="Seconds to consume before exiting")
    parser.add_argument("--group", default="verify-consumer-001")
    args = parser.parse_args()

    schema = load_schema(SCHEMA_PATH)

    consumer = Consumer({
        "bootstrap.servers": args.broker,
        "group.id": args.group,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe([args.topic])

    logger.info("Consuming from %s (broker=%s, timeout=%ds)", args.topic, args.broker, args.timeout)

    count = 0
    decimal_fields = {"change_rate", "vwap", "trade_strength", "buy_ratio", "prev_day_volume_rate", "volume_turnover", "prev_same_hour_volume_rate"}
    start = time.monotonic()

    try:
        while (time.monotonic() - start) < args.timeout:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Consumer error: %s", msg.error())
                break

            key = msg.key().decode("utf-8") if msg.key() else "?"
            headers = {h[0]: h[1].decode("utf-8") for h in (msg.headers() or [])}

            buf = BytesIO(msg.value())
            tick = schemaless_reader(buf, schema)

            count += 1

            decimal_ok = all(isinstance(tick.get(f), Decimal) for f in decimal_fields)

            logger.info(
                "[%d] key=%s session=%s seq=%s | symbol=%s price=%s change_rate=%s vwap=%s | decimal_types_ok=%s",
                count,
                key,
                headers.get("session_id", "?"),
                headers.get("sequence", "?"),
                tick.get("symbol"),
                tick.get("price"),
                tick.get("change_rate"),
                tick.get("vwap"),
                decimal_ok,
            )

            if count == 1:
                logger.info("Full first tick: %s", {k: (type(v).__name__, v) for k, v in tick.items()})

    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        consumer.close()
        logger.info("Total consumed: %d messages in %.1fs", count, time.monotonic() - start)


if __name__ == "__main__":
    main()

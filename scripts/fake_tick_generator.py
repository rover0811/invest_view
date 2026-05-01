"""
Fake tick generator — produces synthetic ParsedTick → Kafka via StockTickProducer.

Usage:
    uv run --project services/kis_ingestion python scripts/fake_tick_generator.py \
        --symbols 005930,000660 --rate 10 --duration 30

Requires: docker-compose.dev.yml Kafka running on localhost:9092
"""

import argparse
import logging
import random
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "kis_ingestion" / "src"))

from kis_ingestion.producer import StockTickProducer
from kis_ingestion.tick_parser import ParsedTick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

SCHEMA_PATH = str(Path(__file__).resolve().parent.parent / "schemas" / "stock-ticks.avsc")

SEED_PRICES = {
    "005930": 73100,   # 삼성전자
    "000660": 198000,  # SK하이닉스
    "035720": 42500,   # 카카오
    "005380": 245000,  # 현대차
    "035420": 192500,  # NAVER
}


def make_tick(symbol: str, base_price: int, seq: int) -> ParsedTick:
    jitter = random.randint(-500, 500)
    price = max(1, base_price + jitter)
    change = jitter
    change_rate = Decimal(str(round(change / base_price * 100, 4)))
    now = time.strftime("%H%M%S")
    today = time.strftime("%Y%m%d")

    return ParsedTick(
        source_tr_id="H0STCNT0",
        market="KRX",
        received_at=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        symbol=symbol,
        trade_time=now,
        price=price,
        change_sign="2" if change >= 0 else "5",
        change=abs(change),
        change_rate=change_rate,
        vwap=Decimal(str(price - random.randint(0, 100))),
        open=base_price,
        high=base_price + random.randint(0, 1000),
        low=base_price - random.randint(0, 1000),
        ask_price_1=price + random.randint(50, 200),
        bid_price_1=price - random.randint(50, 200),
        trade_volume=random.randint(1, 5000),
        cumulative_volume=random.randint(100000, 5000000),
        cumulative_amount=random.randint(1000000000, 50000000000),
        sell_count=random.randint(1000, 50000),
        buy_count=random.randint(1000, 50000),
        net_buy_count=random.randint(-10000, 10000),
        trade_strength=Decimal(str(round(random.uniform(50, 150), 2))),
        total_sell_volume=random.randint(50000, 500000),
        total_buy_volume=random.randint(50000, 500000),
        trade_type="1",
        buy_ratio=Decimal(str(round(random.uniform(0.3, 0.7), 4))),
        prev_day_volume_rate=Decimal(str(round(random.uniform(0.5, 3.0), 4))),
        open_time="090000",
        open_vs_sign="2",
        open_vs_price=random.randint(0, 500),
        high_time="100000",
        high_vs_sign="2",
        high_vs_price=random.randint(0, 1000),
        low_time="093000",
        low_vs_sign="5",
        low_vs_price=random.randint(0, 500),
        business_date=today,
        market_session_code="1",
        trading_halted="0",
        ask_remain_1=random.randint(100, 10000),
        bid_remain_1=random.randint(100, 10000),
        total_ask_remain=random.randint(10000, 500000),
        total_bid_remain=random.randint(10000, 500000),
        volume_turnover=Decimal(str(round(random.uniform(0.01, 1.0), 4))),
        prev_same_hour_volume=random.randint(100000, 2000000),
        prev_same_hour_volume_rate=Decimal(str(round(random.uniform(0.5, 3.0), 4))),
        hour_class_code="1",
        market_termination_code="0",
        vi_trigger_price=base_price + random.randint(1000, 5000),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake tick generator for Kafka E2E testing")
    parser.add_argument("--symbols", default="005930,000660", help="Comma-separated symbol codes")
    parser.add_argument("--rate", type=float, default=10, help="Ticks per second (across all symbols)")
    parser.add_argument("--duration", type=int, default=30, help="Seconds to run (0=infinite)")
    parser.add_argument("--broker", default="localhost:9092", help="Kafka bootstrap servers")
    parser.add_argument("--topic", default="stock-ticks", help="Kafka topic")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    interval = 1.0 / args.rate if args.rate > 0 else 0.1

    logger.info("Starting fake tick generator: symbols=%s rate=%.1f/s duration=%ss", symbols, args.rate, args.duration)
    logger.info("Kafka: broker=%s topic=%s", args.broker, args.topic)

    producer = StockTickProducer(
        bootstrap_servers=args.broker,
        topic=args.topic,
        schema_path=SCHEMA_PATH,
    )

    session_id = str(uuid4())
    sequence = 0
    start = time.monotonic()
    sent = 0

    try:
        while True:
            if args.duration > 0 and (time.monotonic() - start) >= args.duration:
                break

            symbol = random.choice(symbols)
            base_price = SEED_PRICES.get(symbol, 50000)
            sequence += 1

            tick = make_tick(symbol, base_price, sequence)
            producer.publish(tick, session_id, sequence)
            sent += 1

            if sent % 50 == 0:
                logger.info("Sent %d ticks (last: %s @ %d)", sent, tick.symbol, tick.price)

            time.sleep(interval)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        elapsed = time.monotonic() - start
        remaining = producer.flush(10.0)
        logger.info(
            "Done: %d ticks in %.1fs (%.1f/s). Flush remaining: %d",
            sent, elapsed, sent / max(elapsed, 0.001), remaining,
        )


if __name__ == "__main__":
    main()

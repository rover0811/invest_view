#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownParameterType=false, reportUnknownMemberType=false
"""T5.4 E2E synthetic tick producer.

Publishes 3 scenarios to stock-ticks Kafka topic to exercise all 3
detection rules:
  - PRICE_ALERT: 5 ticks within 5-min window with >=5% price spread (70000 -> 73500)
  - VI_IMMINENT: 1 tick where abs(price - vi_trigger_price) / vi_trigger_price < 1% (99500 vs 100000)
  - TRADING_HALT: 2 ticks where trading_halted transitions N -> Y

Usage:
    uv run --project services/kis_ingestion python services/stream_detection_java/scripts/produce_test_ticks.py

For deterministic UUID5/dedup verification, set T5_4_BASE_EPOCH_S to reuse
identical received_at timestamps across runs:
    T5_4_BASE_EPOCH_S=1748600000 uv run --project services/kis_ingestion python services/stream_detection_java/scripts/produce_test_ticks.py
"""
from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services" / "kis_ingestion" / "src"))

from kis_ingestion.producer import StockTickProducer
from kis_ingestion.tick_parser import ParsedTick


def make_tick(
    *,
    symbol: str,
    price: int,
    received_at: str,
    trade_time: str = "030000",
    trading_halted: str = "N",
    vi_trigger_price: int = 0,
    market: str = "KRX",
) -> ParsedTick:
    """Build a ParsedTick with safe defaults; override only rule-relevant fields."""
    return ParsedTick(
        source_tr_id="H0STCNT0",
        market=market,
        received_at=received_at,
        symbol=symbol,
        trade_time=trade_time,
        price=price,
        change_sign="2",
        change=0,
        change_rate=Decimal("0.0"),
        vwap=Decimal("0.0"),
        open=price,
        high=price,
        low=price,
        ask_price_1=price,
        bid_price_1=price,
        trade_volume=1,
        cumulative_volume=1,
        cumulative_amount=1,
        sell_count=0,
        buy_count=0,
        net_buy_count=0,
        trade_strength=Decimal("0.0"),
        total_sell_volume=0,
        total_buy_volume=0,
        trade_type="0",
        buy_ratio=Decimal("0.0"),
        prev_day_volume_rate=Decimal("0.0"),
        open_time="030000",
        open_vs_sign="2",
        open_vs_price=0,
        high_time="030000",
        high_vs_sign="2",
        high_vs_price=0,
        low_time="030000",
        low_vs_sign="5",
        low_vs_price=0,
        business_date="20260531",
        market_session_code="1",
        trading_halted=trading_halted,
        ask_remain_1=0,
        bid_remain_1=0,
        total_ask_remain=0,
        total_bid_remain=0,
        volume_turnover=Decimal("0.0"),
        prev_same_hour_volume=0,
        prev_same_hour_volume_rate=Decimal("0.0"),
        hour_class_code="0",
        market_termination_code="0",
        vi_trigger_price=vi_trigger_price,
    )


def main() -> None:
    producer = StockTickProducer(
        bootstrap_servers="localhost:9092",
        topic="stock-ticks",
        schema_path=str(REPO_ROOT / "schemas" / "stock-ticks.avsc"),
        schema_registry_url="http://localhost:8081",
    )

    base_epoch_s = int(os.environ.get("T5_4_BASE_EPOCH_S", str(int(time.time()))))
    print(f"[T5.4] Using base_epoch_s={base_epoch_s}")

    def t(offset_s: int) -> str:
        """ISO 8601 UTC, ms-precision, Z suffix — kis_ingestion producer format."""
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(base_epoch_s + offset_s))

    print("[T5.4] Scenario 1: PRICE_ALERT (005930, 70000 -> 73500 over 4 min)")
    prices = [70000, 71500, 72500, 73000, 73500]
    for i, price in enumerate(prices):
        tick = make_tick(symbol="005930", price=price, received_at=t(i * 50))
        producer.publish(tick, session_id="t5_4_price", sequence=i)
    producer.flush(timeout=10.0)
    print("  flushed 5 PRICE_ALERT ticks")

    print("[T5.4] Scenario 2: VI_IMMINENT (000660, price=99500 vs vi=100000, ratio=0.005)")
    tick = make_tick(
        symbol="000660",
        price=99500,
        vi_trigger_price=100000,
        received_at=t(300),
    )
    producer.publish(tick, session_id="t5_4_vi", sequence=0)
    producer.flush(timeout=10.0)
    print("  flushed 1 VI_IMMINENT tick")

    print("[T5.4] Scenario 3: TRADING_HALT (035420, N -> Y)")
    for i, halted in enumerate(["N", "Y"]):
        tick = make_tick(
            symbol="035420",
            price=50000,
            trading_halted=halted,
            received_at=t(310 + i * 10),
        )
        producer.publish(tick, session_id="t5_4_halt", sequence=i)
    producer.flush(timeout=10.0)
    print("  flushed 2 TRADING_HALT ticks (N then Y)")

    print("[T5.4] Watermark advancer (005930 at t+360s to fire pending windows)")
    tick = make_tick(symbol="005930", price=73000, received_at=t(360))
    producer.publish(tick, session_id="t5_4_watermark", sequence=0)
    producer.flush(timeout=10.0)

    print("[T5.4] All scenarios published. Wait ~25s for Flink window + alert_service consumer + DB write.")


if __name__ == "__main__":
    main()

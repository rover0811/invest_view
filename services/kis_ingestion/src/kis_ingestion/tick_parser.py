from typing import Any
from decimal import Decimal
from pydantic import BaseModel
from .models.tick import TickRecord


class ParsedTick(BaseModel):
    # Meta fields
    source_tr_id: str
    market: str
    received_at: str

    # 46 tick fields (same as TickRecord)
    symbol: str
    trade_time: str
    price: int
    change_sign: str
    change: int
    change_rate: Decimal
    vwap: Decimal
    open: int
    high: int
    low: int
    ask_price_1: int
    bid_price_1: int
    trade_volume: int
    cumulative_volume: int
    cumulative_amount: int
    sell_count: int
    buy_count: int
    net_buy_count: int
    trade_strength: Decimal
    total_sell_volume: int
    total_buy_volume: int
    trade_type: str
    buy_ratio: Decimal
    prev_day_volume_rate: Decimal
    open_time: str
    open_vs_sign: str
    open_vs_price: int
    high_time: str
    high_vs_sign: str
    high_vs_price: int
    low_time: str
    low_vs_sign: str
    low_vs_price: int
    business_date: str
    market_session_code: str
    trading_halted: str
    ask_remain_1: int
    bid_remain_1: int
    total_ask_remain: int
    total_bid_remain: int
    volume_turnover: Decimal
    prev_same_hour_volume: int
    prev_same_hour_volume_rate: Decimal
    hour_class_code: str
    market_termination_code: str
    vi_trigger_price: int


class KISTickParser:
    def parse(
        self,
        raw_record_values: list[str],
        source_tr_id: str,
        market: str,
        received_at: str
    ) -> ParsedTick:
        """
        Parse a list of 46 string values into a ParsedTick object.
        """
        # Accessing private members of TickRecord as they are the source of truth for field order
        field_count = getattr(TickRecord, "_N")
        field_order = getattr(TickRecord, "_FIELD_ORDER")

        if len(raw_record_values) != field_count:
            raise ValueError(f"Expected {field_count} fields, got {len(raw_record_values)}")

        # Map index to field name
        tick_data: dict[str, Any] = dict(zip(field_order, raw_record_values))

        # Add meta fields
        tick_data["source_tr_id"] = source_tr_id
        tick_data["market"] = market
        tick_data["received_at"] = received_at

        return ParsedTick(**tick_data)

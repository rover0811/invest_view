from decimal import Decimal
from typing import ClassVar, Self

from pydantic import BaseModel

from .constants import SIGN_LABELS


class TickRecord(BaseModel):
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

    # wire index → field name (순서 변경 금지)
    _FIELD_ORDER: ClassVar[list[str]] = [
        "symbol", "trade_time", "price", "change_sign", "change",
        "change_rate", "vwap", "open", "high", "low",
        "ask_price_1", "bid_price_1", "trade_volume", "cumulative_volume",
        "cumulative_amount", "sell_count", "buy_count", "net_buy_count",
        "trade_strength", "total_sell_volume", "total_buy_volume", "trade_type",
        "buy_ratio", "prev_day_volume_rate", "open_time", "open_vs_sign",
        "open_vs_price", "high_time", "high_vs_sign", "high_vs_price",
        "low_time", "low_vs_sign", "low_vs_price", "business_date",
        "market_session_code", "trading_halted", "ask_remain_1", "bid_remain_1",
        "total_ask_remain", "total_bid_remain", "volume_turnover",
        "prev_same_hour_volume", "prev_same_hour_volume_rate",
        "hour_class_code", "market_termination_code", "vi_trigger_price",
    ]
    _N: ClassVar[int] = 46

    @classmethod
    def parse_payload(cls, raw_data: str, data_cnt: int) -> list[Self]:
        """
        Wire: 0|H0STCNT0|004|005930^123929^73100^...
              ┬ ────┬─── ─┬─ ──────────┬──────────
              │     │     │  ^구분 46필드 * data_cnt건
              │     │     └─ 데이터 건수
              │     └─ TR_ID
              └─ 암호화 (0=평문, 1=AES256)
        """
        vals = raw_data.split("^")
        return [
            cls(**dict(zip(cls._FIELD_ORDER, vals[i * cls._N : (i + 1) * cls._N])))
            for i in range(data_cnt)
            if len(vals[i * cls._N : (i + 1) * cls._N]) == cls._N
        ]

    def format_line(self) -> str:
        t = self.trade_time
        sign = SIGN_LABELS.get(self.change_sign, "?")
        return (
            f"[{t[:2]}:{t[2:4]}:{t[4:6]}] {self.symbol}  "
            f"현재가: {self.price:>8,}  {sign} {self.change:>+7,} ({self.change_rate:>6}%)  "
            f"체결량: {self.trade_volume:>8,}  누적: {self.cumulative_volume:>12,}  "
            f"체결강도: {self.trade_strength}"
        )

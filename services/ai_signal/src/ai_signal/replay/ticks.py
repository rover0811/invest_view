"""Convert a 1-minute bar into a stock-ticks Avro record dict."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from ai_signal.replay.bars import Bar

SOURCE_TR_ID = "REPLAY_BAR"
_SCALE = Decimal("0.00000001")
_ZERO = Decimal("0").quantize(_SCALE)


def _sign(diff: int) -> str:
    if diff > 0:
        return "2"
    if diff < 0:
        return "5"
    return "3"


def _dec(value: Decimal) -> Decimal:
    return value.quantize(_SCALE, rounding=ROUND_HALF_UP)


def bar_to_tick(bar: Bar, prev_close: int, symbol: str) -> dict[str, object]:
    change = bar.close - prev_close
    rate = _dec(Decimal(change) * 100 / Decimal(prev_close)) if prev_close else _ZERO
    trade_time = bar.t.strftime("%H%M%S")
    business_date = bar.t.strftime("%Y%m%d")
    return {
        "source_tr_id": SOURCE_TR_ID,
        "market": "KRX",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "event_id": f"replay:{symbol}:{business_date}{trade_time}",
        "symbol": symbol,
        "trade_time": trade_time,
        "price": bar.close,
        "change_sign": _sign(change),
        "change": change,
        "change_rate": rate,
        "vwap": _dec(Decimal(bar.close)),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "ask_price_1": bar.close,
        "bid_price_1": bar.close,
        "trade_volume": bar.volume,
        "cumulative_volume": bar.volume,
        "cumulative_amount": bar.volume * bar.close,
        "sell_count": 0,
        "buy_count": 0,
        "net_buy_count": 0,
        "trade_strength": _ZERO,
        "total_sell_volume": 0,
        "total_buy_volume": 0,
        "trade_type": "",
        "buy_ratio": _ZERO,
        "prev_day_volume_rate": _ZERO,
        "open_time": trade_time,
        "open_vs_sign": _sign(bar.close - bar.open),
        "open_vs_price": bar.close - bar.open,
        "high_time": trade_time,
        "high_vs_sign": _sign(bar.close - bar.high),
        "high_vs_price": bar.close - bar.high,
        "low_time": trade_time,
        "low_vs_sign": _sign(bar.close - bar.low),
        "low_vs_price": bar.close - bar.low,
        "business_date": business_date,
        "market_session_code": "0",
        "trading_halted": "N",
        "ask_remain_1": 0,
        "bid_remain_1": 0,
        "total_ask_remain": 0,
        "total_bid_remain": 0,
        "volume_turnover": _ZERO,
        "prev_same_hour_volume": 0,
        "prev_same_hour_volume_rate": _ZERO,
        "hour_class_code": "0",
        "market_termination_code": "",
        "vi_trigger_price": 0,
    }

from decimal import Decimal
from typing import ClassVar, Self

from pydantic import BaseModel


class OrderBookRecord(BaseModel):
    symbol: str
    business_time: str
    hour_class_code: str
    ask_prices: list[int]
    bid_prices: list[int]
    ask_volumes: list[int]
    bid_volumes: list[int]
    total_ask_remain: int
    total_bid_remain: int
    overtime_total_ask: int
    overtime_total_bid: int
    expected_price: int
    expected_quantity: int
    expected_volume: int
    expected_change: int
    expected_change_sign: str
    expected_change_rate: Decimal
    cumulative_volume: int
    total_ask_remain_change: Decimal
    total_bid_remain_change: Decimal
    overtime_ask_change: Decimal
    overtime_bid_change: Decimal

    _N: ClassVar[int] = 54

    @classmethod
    def parse_payload(cls, raw_data: str, data_cnt: int) -> list[Self]:
        vals = raw_data.split("^")
        records: list[Self] = []
        for i in range(data_cnt):
            chunk = vals[i * cls._N : (i + 1) * cls._N]
            if len(chunk) < cls._N:
                break
            records.append(cls(
                symbol=chunk[0],
                business_time=chunk[1],
                hour_class_code=chunk[2],
                ask_prices=[int(chunk[j]) for j in range(3, 12)],
                bid_prices=[int(chunk[j]) for j in range(12, 21)],
                ask_volumes=[int(chunk[j]) for j in range(21, 30)],
                bid_volumes=[int(chunk[j]) for j in range(30, 39)],
                total_ask_remain=int(chunk[39]),
                total_bid_remain=int(chunk[40]),
                overtime_total_ask=int(chunk[41]),
                overtime_total_bid=int(chunk[42]),
                expected_price=int(chunk[43]),
                expected_quantity=int(chunk[44]),
                expected_volume=int(chunk[45]),
                expected_change=int(chunk[46]),
                expected_change_sign=chunk[47],
                expected_change_rate=Decimal(chunk[48]),
                cumulative_volume=int(chunk[49]),
                total_ask_remain_change=Decimal(chunk[50]),
                total_bid_remain_change=Decimal(chunk[51]),
                overtime_ask_change=Decimal(chunk[52]),
                overtime_bid_change=Decimal(chunk[53]),
            ))
        return records

    def format_line(self) -> str:
        t = self.business_time
        time_str = f"{t[:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 else t
        lines = [f"[{time_str}] {self.symbol}  ── 호가 ──"]
        for i in reversed(range(9)):
            lines.append(
                f"  매도 {i+1:>2}  {self.ask_prices[i]:>8,}  [{self.ask_volumes[i]:>10,}]"
            )
        lines.append("  " + "-" * 40)
        for i in range(9):
            lines.append(
                f"  매수 {i+1:>2}  {self.bid_prices[i]:>8,}  [{self.bid_volumes[i]:>10,}]"
            )
        lines.append(
            f"  총매도잔량: {self.total_ask_remain:>12,}  "
            f"총매수잔량: {self.total_bid_remain:>12,}"
        )
        return "\n".join(lines)

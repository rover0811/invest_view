from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SurgePlungeThresholds:
    surge_return: Decimal = Decimal("0.05")
    plunge_return: Decimal = Decimal("-0.05")

    def classify(self, return_rate: Decimal) -> str | None:
        if return_rate >= self.surge_return:
            return "SURGE"
        if return_rate <= self.plunge_return:
            return "PLUNGE"
        return None


@dataclass(frozen=True)
class MovingAverageCross:
    short_period: int = 5
    long_period: int = 20

    def detect(self, prev_short: Decimal, prev_long: Decimal, cur_short: Decimal, cur_long: Decimal) -> str | None:
        if prev_short <= prev_long and cur_short > cur_long:
            return "GOLDEN_CROSS"
        if prev_short >= prev_long and cur_short < cur_long:
            return "DEAD_CROSS"
        return None

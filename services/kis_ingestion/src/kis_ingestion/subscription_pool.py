from collections.abc import Iterable


class KISSubscriptionPool:
    cap: int

    def __init__(self, cap: int = 40):
        self.cap = cap
        self._desired: set[tuple[str, str]] = set()
        self._actual: set[tuple[str, str]] = set()

    def set_desired(self, symbols: Iterable[str], tr_id: str) -> None:
        new_desired: set[tuple[str, str]] = set(item for item in self._desired if item[0] != tr_id)
        
        for symbol in symbols:
            new_desired.add((tr_id, symbol))
            
        if len(new_desired) > self.cap:
            raise ValueError(f"Subscription cap exceeded: {len(new_desired)} > {self.cap}")
            
        self._desired = new_desired

    def confirm_subscribed(self, tr_id: str, symbol: str) -> None:
        self._actual.add((tr_id, symbol))

    def confirm_unsubscribed(self, tr_id: str, symbol: str) -> None:
        self._actual.discard((tr_id, symbol))

    def diff(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        to_subscribe = list(self._desired - self._actual)
        to_unsubscribe = list(self._actual - self._desired)
        return to_subscribe, to_unsubscribe

    def switch_market(self, old_tr_id: str, new_tr_id: str) -> None:
        new_desired: set[tuple[str, str]] = set()
        for tr_id, symbol in self._desired:
            if tr_id == old_tr_id:
                new_desired.add((new_tr_id, symbol))
            else:
                new_desired.add((tr_id, symbol))
        self._desired = new_desired

    def clear_actual(self) -> None:
        self._actual.clear()

    @property
    def desired_count(self) -> int:
        return len(self._desired)

    @property
    def actual_count(self) -> int:
        return len(self._actual)

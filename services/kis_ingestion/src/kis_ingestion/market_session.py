from datetime import datetime, time
from typing import Protocol


class MarketSessionPort(Protocol):
    @property
    def tick_tr_id(self) -> str: ...

    @property
    def hoga_tr_id(self) -> str: ...

    @property
    def market_name(self) -> str: ...


class KRXSessionAdapter:
    @property
    def tick_tr_id(self) -> str:
        return "H0STCNT0"

    @property
    def hoga_tr_id(self) -> str:
        return "H0STASP0"

    @property
    def market_name(self) -> str:
        return "KRX"


class NXTSessionAdapter:
    @property
    def tick_tr_id(self) -> str:
        return "H0NXCNT0"

    @property
    def hoga_tr_id(self) -> str:
        return "H0NXASP0"

    @property
    def market_name(self) -> str:
        return "NXT"


class MarketSessionRouter:
    _active: MarketSessionPort

    def __init__(self, initial_adapter: MarketSessionPort):
        self._active = initial_adapter

    @property
    def active(self) -> MarketSessionPort:
        return self._active

    @property
    def tick_tr_id(self) -> str:
        return self._active.tick_tr_id

    @property
    def hoga_tr_id(self) -> str:
        return self._active.hoga_tr_id

    @property
    def market_name(self) -> str:
        return self._active.market_name

    def switch(self, new_adapter: MarketSessionPort) -> tuple[str, str]:
        old_tick_tr_id = self._active.tick_tr_id
        self._active = new_adapter
        return old_tick_tr_id, self._active.tick_tr_id


class ScheduleBasedSessionSwitcher:
    def determine_initial_market(self, now: datetime | None = None) -> MarketSessionPort:
        if now is None:
            now = datetime.now()

        current_time = now.time()

        # 08:00-09:00 -> NXT (pre-market)
        if time(8, 0) <= current_time < time(9, 0):
            return NXTSessionAdapter()

        # 09:00-15:30 -> KRX (regular hours, KRX priority)
        if time(9, 0) <= current_time < time(15, 30):
            return KRXSessionAdapter()

        # 15:30-20:00 -> NXT (after-hours)
        if time(15, 30) <= current_time < time(20, 0):
            return NXTSessionAdapter()

        # Outside 08:00-20:00 -> KRX (default fallback)
        return KRXSessionAdapter()

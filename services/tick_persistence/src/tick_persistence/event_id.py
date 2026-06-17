from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Protocol


TICK_EVENT_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
TICK_EVENT_ID_FIELDS = (
    "market",
    "symbol",
    "business_date",
    "cumulative_volume",
    "trade_time",
    "price",
    "trade_type",
)


class TickModelLike(Protocol):
    def model_dump(self) -> dict[str, object]: ...


TickInput = Mapping[str, object] | TickModelLike


def _as_mapping(tick: TickInput) -> Mapping[str, object]:
    if isinstance(tick, Mapping):
        return tick
    return tick.model_dump()


def _required(tick: Mapping[str, object], field: str) -> object:
    value = tick.get(field)
    if value is None:
        raise ValueError(f"missing required tick identity field: {field}")
    return value


def _clean_str(value: object, field: str) -> str:
    text = str(value).strip()
    if text == "":
        raise ValueError(f"empty tick identity field: {field}")
    return text


def _int_string(value: object, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"invalid boolean for integer tick identity field: {field}")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        try:
            return str(int(value.strip()))
        except ValueError as exc:
            raise ValueError(f"invalid integer tick identity field: {field}={value!r}") from exc

    raise ValueError(f"invalid integer tick identity field: {field}={value!r}")


def compute_event_id(tick: TickInput) -> str:
    tick_mapping = _as_mapping(tick)
    market_value = tick_mapping.get("market")
    if market_value is None:
        market_value = _required(tick_mapping, "source_tr_id")

    normalized = {
        "market": _clean_str(market_value, "market").upper(),
        "symbol": _clean_str(_required(tick_mapping, "symbol"), "symbol"),
        "business_date": _clean_str(_required(tick_mapping, "business_date"), "business_date"),
        "cumulative_volume": _int_string(_required(tick_mapping, "cumulative_volume"), "cumulative_volume"),
        "trade_time": _clean_str(_required(tick_mapping, "trade_time"), "trade_time"),
        "price": _int_string(_required(tick_mapping, "price"), "price"),
        "trade_type": _clean_str(_required(tick_mapping, "trade_type"), "trade_type"),
    }
    name_string = "|".join(normalized[field] for field in TICK_EVENT_ID_FIELDS)
    return str(uuid.uuid5(TICK_EVENT_NAMESPACE, name_string))

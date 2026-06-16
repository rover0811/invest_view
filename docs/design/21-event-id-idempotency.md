# 21. Event ID idempotency contract — stock tick SoT

This is the source of truth for the deterministic `event_id` / `tick_dedupe_key` contract used by `stock-ticks`. `kis_ingestion` and `tick_persistence` must be able to implement this note independently and produce byte-identical UUID strings for the same logical tick.

## 1. Exact key specification

### Ordered identity tuple

The UUIDv5 name string is built from exactly these seven fields, in this order:

```text
market|symbol|business_date|cumulative_volume|trade_time|price|trade_type
```

Field order is part of the contract. Do not reorder, omit, append, or rename components when computing the id.

### Separator

- Separator: the literal pipe character, `|`.
- The separator is inserted between normalized string components only; there is no leading or trailing separator.

### Namespace and algorithm

- UUID namespace: DNS namespace UUID `6ba7b810-9dad-11d1-80b4-00c04fd430c8`.
- Algorithm: UUIDv5 over the exact pipe-delimited name string.
- Python equivalent: `uuid.uuid5(uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"), name_string)`.
- Persist and serialize the canonical lowercase UUID string returned by `str(...)`, unless a DB UUID column is used internally.

### Per-field normalization rules

All components are normalized first, then converted to `str`, then joined.

| Position | Field | Required input type / meaning | Normalization before join |
| --- | --- | --- | --- |
| 1 | `market` | Market/source discriminator string | Trim surrounding whitespace, uppercase. Expected canonical values include `KRX` and `NXT`. If a consumer-side record lacks `market`, use `source_tr_id` (`H0STCNT0` / `H0NXCNT0`) as this first component; never omit the market/source discriminator. |
| 2 | `symbol` | Korean stock code string | Trim surrounding whitespace. Preserve leading zeroes, e.g. `005930`; never parse as int. |
| 3 | `business_date` | `YYYYMMDD` string | Trim surrounding whitespace. Keep the 8-digit string as-is after validation; do not parse/reformat through a date object for id construction. |
| 4 | `cumulative_volume` | Integer cumulative traded volume | Convert to base-10 integer string with no commas and no zero padding, e.g. `123456`. |
| 5 | `trade_time` | `HHMMSS` string | Trim surrounding whitespace. Keep the 6-digit string as-is after validation; do not parse/reformat through a time object for id construction. |
| 6 | `price` | Integer trade price | Convert to base-10 integer string with no commas and no zero padding, e.g. `70100`. |
| 7 | `trade_type` | KIS execution classification code string | Trim surrounding whitespace. Preserve the source code value; do not translate labels or map aliases. |

Missing or null required fields are hard errors. Producers and consumers must fail the insert/publish path explicitly rather than falling back to Kafka offsets, wall-clock time, `received_at`, `persisted_at`, `uuid4`, or random/session identity.

## 2. Reference Python implementation

Both services may share this helper or duplicate it identically. The implementation below is normative for string construction and UUID generation.

```python
from __future__ import annotations

import uuid
from typing import Any


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


def _required(tick: dict[str, Any], field: str) -> Any:
    value = tick.get(field)
    if value is None:
        raise ValueError(f"missing required tick identity field: {field}")
    return value


def _clean_str(value: Any, field: str) -> str:
    text = str(value).strip()
    if text == "":
        raise ValueError(f"empty tick identity field: {field}")
    return text


def _int_string(value: Any, field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"invalid boolean for integer tick identity field: {field}")
    try:
        return str(int(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid integer tick identity field: {field}={value!r}") from exc


def compute_event_id(tick: dict[str, Any]) -> str:
    market_value = tick.get("market")
    if market_value is None:
        market_value = _required(tick, "source_tr_id")

    normalized = {
        "market": _clean_str(market_value, "market").upper(),
        "symbol": _clean_str(_required(tick, "symbol"), "symbol"),
        "business_date": _clean_str(_required(tick, "business_date"), "business_date"),
        "cumulative_volume": _int_string(_required(tick, "cumulative_volume"), "cumulative_volume"),
        "trade_time": _clean_str(_required(tick, "trade_time"), "trade_time"),
        "price": _int_string(_required(tick, "price"), "price"),
        "trade_type": _clean_str(_required(tick, "trade_type"), "trade_type"),
    }
    name_string = "|".join(normalized[field] for field in TICK_EVENT_ID_FIELDS)
    return str(uuid.uuid5(TICK_EVENT_NAMESPACE, name_string))
```

Example canonical name string:

```text
KRX|005930|20260617|123456|091530|70100|2
```

## 3. Existing UUIDv5 convention mirrored

This contract intentionally mirrors the repository's existing deterministic pattern-event convention: DNS namespace UUIDv5 plus pipe-delimited stable identity fields.

Quoted code being mirrored:

```java
// services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:16-18
private static final UUID NAMESPACE_DNS = UUID.fromString("6ba7b810-9dad-11d1-80b4-00c04fd430c8");
private static final NameBasedGenerator UUID5_GEN = Generators.nameBasedGenerator(NAMESPACE_DNS);

// services/stream_detection_java/src/main/java/com/invest_view/stream_detection/alert/PatternBuilders.java:26-28
public static String makePatternEventId(String symbol, String patternType, String windowKey) {
    String name = symbol + "|" + patternType + "|" + windowKey;
    return UUID5_GEN.generate(name).toString();
}
```

The corresponding sink treats that deterministic id as the idempotency key:

```python
# services/event_pattern_persistence/src/event_pattern_persistence/repository/pattern_events.py:53-60
async def insert(self, pattern: dict[str, Any]) -> None:
    """Insert pattern if new; duplicate pattern_event_id is a no-op (UUIDv5 idempotency)."""
    row = _to_row(pattern)
    async with self._sf() as session:
        stmt = (
            pg_insert(PatternEvent)
            .values(**row)
            .on_conflict_do_nothing(index_elements=["pattern_event_id"])
        )
```

`services/event_pattern_persistence/tests/test_avro_roundtrip.py:41-42` also pins use of `uuid.uuid5(uuid.NAMESPACE_DNS, ...)` for pattern ids; the tick id contract follows the production Java builder's explicit DNS namespace UUID and pipe delimiter.

## 4. Collision policy and residual risk

The core surrogate is `cumulative_volume` scoped by `market`, `symbol`, and `business_date`. For normal positive-volume execution ticks, KIS cumulative volume is expected to advance with each observable execution event; if KIS aggregates multiple executions into one emitted tick, that aggregate is the observable source event and intentionally maps to one id.

Two distinct observable ticks could still collide if KIS emits zero-volume correction/status ticks, repeated correction ticks, or other records that reuse the same `(market, symbol, business_date, cumulative_volume)` tuple. Adding `trade_time`, `price`, and `trade_type` mitigates this by distinguishing same-volume events that differ in execution time, price, or KIS classification. The residual risk is explicit: two distinct KIS events with identical values for all seven fields will produce the same UUIDv5 name string and will be deduplicated as one logical tick. This is accepted for this phase because KIS exposes no execution number/sequence in the current `stock-ticks` contract, and full-payload hashing is rejected as unstable logical identity.

## 5. DDIA grounding

DDIA Ch11 frames stream event identity as distinct from log position: Kafka topic/partition/offset is lineage, not the business identity of a tick, so replay or republish must not create a new id for the same logical event. DDIA Ch11 idempotence requires at-least-once consumers to map repeated delivery to the same sink operation/key; `ON CONFLICT DO NOTHING` is only idempotent when the conflict target is the logical event id. DDIA Ch12 end-to-end thinking means broker-level producer idempotence is insufficient: the DB sink must be able to recompute the same id from event content after producer restart, consumer crash, offset reset, or republish.

## 6. Generation and enforcement points

- Generation point: `kis_ingestion` producer, after KIS tick normalization and before Avro serialization. The generated UUID string must be placed in the tick event payload field that T9 adds for this contract.
- Consumer recomputation/validation point: `tick_persistence` may recompute the id from the received tick using the same helper to validate producer output or to support migration/replay paths, but it must not invent a different identity.
- Enforcement point: Bronze persistence. `bronze.tick_history` must enforce uniqueness on the deterministic tick id / `tick_dedupe_key`, and inserts must use `ON CONFLICT DO NOTHING` against that unique key.
- Kafka coordinates (`topic`, `partition`, `offset`) remain audit lineage columns only and must not participate in event id construction.

"""SymbolSnapshot repository — keep one latest-state row per symbol in serving."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.db.models import SymbolSnapshot

KST = ZoneInfo("Asia/Seoul")


def _last_event_ts_for(tick: Mapping[str, object]) -> datetime:
    business_date = str(tick["business_date"]).strip()
    trade_time = str(tick["trade_time"]).strip()
    return datetime.strptime(f"{business_date}{trade_time}", "%Y%m%d%H%M%S").replace(tzinfo=KST)


def _snapshot_values_for(tick: Mapping[str, object], last_event_ts: datetime) -> dict[str, object]:
    return {
        "symbol": tick["symbol"],
        "last_price": tick.get("price"),
        "change": tick.get("change"),
        "change_rate": tick.get("change_rate"),
        "change_sign": tick.get("change_sign"),
        "cumulative_volume": tick.get("cumulative_volume"),
        "trade_strength": tick.get("trade_strength"),
        "vi_trigger_price": tick.get("vi_trigger_price"),
        "trading_halted": tick.get("trading_halted"),
        "last_trade_time": tick.get("trade_time"),
        "business_date": tick.get("business_date"),
        "last_event_ts": last_event_ts,
        "updated_at": func.now(),
    }


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"unsupported lineage value: {value!r} ({type(value)})")


def _lineage_value(tick: Mapping[str, object], name: str) -> int | None:
    direct_value = tick.get(name)
    if direct_value is not None:
        return _optional_int(direct_value)
    return _optional_int(tick.get(f"kafka_{name}"))


def _dedupe_key(tick: Mapping[str, object], last_event_ts: datetime, index: int) -> tuple[datetime, int, int, int]:
    partition = _lineage_value(tick, "partition")
    offset = _lineage_value(tick, "offset")
    if partition is None or offset is None:
        return (last_event_ts, -1, -1, index)
    return (last_event_ts, partition, offset, index)


def _deduplicated_snapshot_values(ticks: list[Mapping[str, object]]) -> list[dict[str, object]]:
    latest: dict[str, tuple[tuple[datetime, int, int, int], dict[str, object]]] = {}
    for index, tick in enumerate(ticks):
        symbol = str(tick["symbol"])
        last_event_ts = _last_event_ts_for(tick)
        key = _dedupe_key(tick, last_event_ts, index)
        values = _snapshot_values_for(tick, last_event_ts)
        current = latest.get(symbol)
        if current is None or key > current[0]:
            latest[symbol] = (key, values)
    return [values for _, values in latest.values()]


class SnapshotRepository:
    async def upsert_snapshot(self, session: AsyncSession, tick: Mapping[str, object]) -> None:
        await self.upsert_snapshots(session, [tick])

    async def upsert_snapshots(self, session: AsyncSession, ticks: list[Mapping[str, object]]) -> None:
        values = _deduplicated_snapshot_values(ticks)
        if not values:
            return

        stmt = pg_insert(SymbolSnapshot).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol"],
            set_={
                "last_price": stmt.excluded.last_price,
                "change": stmt.excluded.change,
                "change_rate": stmt.excluded.change_rate,
                "change_sign": stmt.excluded.change_sign,
                "cumulative_volume": stmt.excluded.cumulative_volume,
                "trade_strength": stmt.excluded.trade_strength,
                "vi_trigger_price": stmt.excluded.vi_trigger_price,
                "trading_halted": stmt.excluded.trading_halted,
                "last_trade_time": stmt.excluded.last_trade_time,
                "business_date": stmt.excluded.business_date,
                "last_event_ts": stmt.excluded.last_event_ts,
                "updated_at": func.now(),
            },
            where=or_(
                SymbolSnapshot.last_event_ts.is_(None),
                stmt.excluded.last_event_ts >= SymbolSnapshot.last_event_ts,
            ),
        )
        _ = await session.execute(stmt)

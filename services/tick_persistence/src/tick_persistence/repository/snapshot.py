"""SymbolSnapshot repository — keep one latest-state row per symbol in serving."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.db.models import SymbolSnapshot


class SnapshotRepository:
    async def upsert_snapshot(self, session: AsyncSession, tick: dict[str, Any]) -> None:
        stmt = pg_insert(SymbolSnapshot).values(
            symbol=tick["symbol"],
            last_price=tick.get("price"),
            change=tick.get("change"),
            change_rate=tick.get("change_rate"),
            change_sign=tick.get("change_sign"),
            cumulative_volume=tick.get("cumulative_volume"),
            trade_strength=tick.get("trade_strength"),
            vi_trigger_price=tick.get("vi_trigger_price"),
            trading_halted=tick.get("trading_halted"),
            last_trade_time=tick.get("trade_time"),
            updated_at=func.now(),
        )
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
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)

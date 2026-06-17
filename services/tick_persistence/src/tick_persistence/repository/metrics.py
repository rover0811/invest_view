"""Symbol5mMetrics repository — idempotent silver 5m-bar upsert + restart recovery load."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.aggregation.ohlc import BUCKET_SIZE, BarState
from tick_persistence.db.models import Symbol5mMetrics, TickHistory


class Metrics5mRepository:
    async def upsert_bar(
        self,
        session: AsyncSession,
        symbol: str,
        bucket_start: datetime,
        bucket_end: datetime | None,
        bar: BarState,
    ) -> None:
        stmt = pg_insert(Symbol5mMetrics).values(
            symbol=symbol,
            bucket_start=bucket_start,
            bucket_end=bucket_end,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            vwap=bar.vwap_last,
            tick_count=bar.tick_count,
            is_final=bar.is_final,
            updated_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "bucket_start"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "vwap": stmt.excluded.vwap,
                "tick_count": stmt.excluded.tick_count,
                "is_final": stmt.excluded.is_final,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)

    async def load_bar_state(
        self, session: AsyncSession, symbol: str, bucket_start: datetime
    ) -> BarState | None:
        """Rebuild an active bar from bronze rows so open/close order keys survive restart."""
        result = await session.execute(
            select(
                TickHistory.event_ts,
                TickHistory.kafka_partition,
                TickHistory.kafka_offset,
                TickHistory.price,
                TickHistory.trade_volume,
                TickHistory.vwap,
            ).where(
                TickHistory.symbol == symbol,
                TickHistory.event_ts >= bucket_start,
                TickHistory.event_ts < bucket_start + BUCKET_SIZE,
            )
        )
        bar: BarState | None = None
        for event_ts, partition, offset, price, volume, vwap in result.all():
            if event_ts is None or partition is None or offset is None or price is None or volume is None:
                continue
            tick_key = (event_ts, partition, offset)
            if bar is None:
                bar = BarState.from_tick(price=price, volume=volume, vwap=vwap, tick_key=tick_key)
            else:
                bar.add_tick(price=price, volume=volume, vwap=vwap, tick_key=tick_key)
        return bar

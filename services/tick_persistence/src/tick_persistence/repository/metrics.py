"""Symbol5mMetrics repository — idempotent silver 5m-bar upsert + restart recovery load."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tick_persistence.aggregation.ohlc import BarState
from tick_persistence.db.models import Symbol5mMetrics


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
        """Hydrate a persisted bar for restart-mid-bucket recovery; None if the bucket is absent."""
        result = await session.execute(
            select(Symbol5mMetrics).where(
                Symbol5mMetrics.symbol == symbol,
                Symbol5mMetrics.bucket_start == bucket_start,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        open_ = row.open
        high = row.high
        low = row.low
        close = row.close
        volume = row.volume
        tick_count = row.tick_count
        if open_ is None or high is None or low is None or close is None or volume is None or tick_count is None:
            return None
        return BarState.from_existing(
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            vwap=row.vwap,
            tick_count=tick_count,
            is_final=row.is_final,
        )

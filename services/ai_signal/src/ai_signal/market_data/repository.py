"""Reads and idempotent writes for the market data job.

Every write targets the business key of its table (doc 20, idempotency
contract): re-running a day rewrites the same rows. ``known_at`` changes only
when the stored value actually changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from ai_signal.market_data.fetch import ConstituentWeight, DailyClose

KST = ZoneInfo("Asia/Seoul")
KRX_CLOSE = time(15, 30)
DAILY_SOURCE = "KIS_DAILY"
MARKET_INDEX_TICKER = "0001"


@dataclass(frozen=True)
class IndexRef:
    entity_id: UUID
    ticker: str
    exchange_code: str
    index_kind: str


@dataclass(frozen=True)
class SymbolRef:
    entity_id: UUID
    ticker: str
    sector_code: str | None
    proxy_etf_entity_id: UUID | None


@dataclass(frozen=True)
class Universe:
    indices: tuple[IndexRef, ...]
    symbols: tuple[SymbolRef, ...]

    @property
    def domestic(self) -> tuple[IndexRef, ...]:
        return tuple(i for i in self.indices if i.index_kind == "DOMESTIC_INDEX")

    @property
    def us_etfs(self) -> tuple[IndexRef, ...]:
        return tuple(i for i in self.indices if i.index_kind == "US_ETF")

    def domestic_by_ticker(self, ticker: str) -> IndexRef | None:
        return next((i for i in self.domestic if i.ticker == ticker), None)

    def us_by_ticker(self, ticker: str) -> IndexRef | None:
        return next((i for i in self.us_etfs if i.ticker == ticker), None)


def daily_observed_at(business_date: date) -> datetime:
    return datetime.combine(business_date, KRX_CLOSE, tzinfo=KST)


async def load_universe(conn: AsyncConnection) -> Universe:
    index_rows = await conn.execute(
        text(
            "SELECT entity_id, ticker, exchange_code, index_kind "
            "FROM reference.entity_index ORDER BY index_kind, ticker"
        )
    )
    indices = tuple(IndexRef(*row) for row in index_rows.all())
    symbol_rows = await conn.execute(
        text(
            """
            SELECT s.entity_id, s.ticker, sec.code, proxy.to_entity_id
            FROM reference.entity_symbol s
            LEFT JOIN LATERAL (
                SELECT r.to_entity_id
                FROM reference.entity_relation r
                WHERE r.from_entity_id = s.entity_id
                  AND r.relation_type = 'BELONGS_TO'
                  AND r.valid_from <= now()
                  AND (r.valid_to IS NULL OR r.valid_to > now())
                ORDER BY r.valid_from DESC
                LIMIT 1
            ) belongs ON true
            LEFT JOIN reference.entity_sector sec ON sec.entity_id = belongs.to_entity_id
            LEFT JOIN LATERAL (
                SELECT r.to_entity_id
                FROM reference.entity_relation r
                JOIN reference.entity_index i ON i.entity_id = r.to_entity_id
                WHERE r.from_entity_id = belongs.to_entity_id
                  AND r.relation_type = 'PROXIED_BY'
                  AND i.index_kind = 'US_ETF'
                  AND r.valid_from <= now()
                  AND (r.valid_to IS NULL OR r.valid_to > now())
                ORDER BY r.valid_from DESC
                LIMIT 1
            ) proxy ON true
            WHERE s.market = 'KOSPI'
            ORDER BY s.ticker
            """
        )
    )
    symbols = tuple(SymbolRef(*row) for row in symbol_rows.all())
    return Universe(indices=indices, symbols=symbols)


async def upsert_index_values(
    conn: AsyncConnection,
    index_entity_id: UUID,
    rows: Sequence[DailyClose],
    known_at: datetime,
) -> None:
    if not rows:
        return
    await conn.execute(
        text(
            """
            INSERT INTO reference.index_value
                (index_entity_id, observed_at, business_date, value, source, known_at)
            VALUES (:index_entity_id, :observed_at, :business_date, :value, :source, :known_at)
            ON CONFLICT (index_entity_id, observed_at) DO UPDATE
            SET business_date = EXCLUDED.business_date,
                value = EXCLUDED.value,
                source = EXCLUDED.source,
                known_at = EXCLUDED.known_at
            WHERE reference.index_value.value IS DISTINCT FROM EXCLUDED.value
               OR reference.index_value.source IS DISTINCT FROM EXCLUDED.source
            """
        ),
        [
            {
                "index_entity_id": index_entity_id,
                "observed_at": daily_observed_at(row.business_date),
                "business_date": row.business_date,
                "value": row.close,
                "source": DAILY_SOURCE,
                "known_at": known_at,
            }
            for row in rows
        ],
    )


async def upsert_us_etf_daily(
    conn: AsyncConnection,
    index_entity_id: UUID,
    rows: Sequence[DailyClose],
    known_at: datetime,
) -> None:
    if not rows:
        return
    await conn.execute(
        text(
            """
            INSERT INTO reference.us_etf_daily
                (index_entity_id, business_date, close, source, known_at)
            VALUES (:index_entity_id, :business_date, :close, :source, :known_at)
            ON CONFLICT (index_entity_id, business_date) DO UPDATE
            SET close = EXCLUDED.close,
                source = EXCLUDED.source,
                known_at = EXCLUDED.known_at
            WHERE reference.us_etf_daily.close IS DISTINCT FROM EXCLUDED.close
            """
        ),
        [
            {
                "index_entity_id": index_entity_id,
                "business_date": row.business_date,
                "close": row.close,
                "source": DAILY_SOURCE,
                "known_at": known_at,
            }
            for row in rows
        ],
    )


async def upsert_constituent_weights(
    conn: AsyncConnection,
    index_entity_id: UUID,
    as_of_date: date,
    weights: Sequence[ConstituentWeight],
    symbol_ids: dict[str, UUID],
    known_at: datetime,
) -> int:
    params = [
        {
            "index_entity_id": index_entity_id,
            "symbol_entity_id": symbol_ids[w.ticker],
            "as_of_date": as_of_date,
            "weight": w.weight,
            "market_cap": w.market_cap_100m_krw,
            "known_at": known_at,
        }
        for w in weights
        if w.ticker in symbol_ids
    ]
    if not params:
        return 0
    await conn.execute(
        text(
            """
            INSERT INTO reference.index_constituent_weight
                (index_entity_id, symbol_entity_id, as_of_date, weight,
                 market_cap_100m_krw, known_at)
            VALUES (:index_entity_id, :symbol_entity_id, :as_of_date, :weight,
                    :market_cap, :known_at)
            ON CONFLICT (index_entity_id, symbol_entity_id, as_of_date) DO UPDATE
            SET weight = EXCLUDED.weight,
                market_cap_100m_krw = EXCLUDED.market_cap_100m_krw,
                known_at = EXCLUDED.known_at
            WHERE reference.index_constituent_weight.weight IS DISTINCT FROM EXCLUDED.weight
               OR reference.index_constituent_weight.market_cap_100m_krw
                  IS DISTINCT FROM EXCLUDED.market_cap_100m_krw
            """
        ),
        params,
    )
    return len(params)


async def load_index_closes(
    conn: AsyncConnection, index_ids: Sequence[UUID], start: date, end: date
) -> dict[UUID, pd.Series]:
    result = await conn.execute(
        text(
            """
            SELECT index_entity_id, business_date, value
            FROM reference.index_value
            WHERE index_entity_id = ANY(:ids) AND source = :source
              AND business_date BETWEEN :start AND :end
            ORDER BY business_date
            """
        ),
        {"ids": list(index_ids), "source": DAILY_SOURCE, "start": start, "end": end},
    )
    return _series_by_key(result.all())


async def load_us_closes(
    conn: AsyncConnection, index_ids: Sequence[UUID], start: date, end: date
) -> dict[UUID, pd.Series]:
    result = await conn.execute(
        text(
            """
            SELECT index_entity_id, business_date, close
            FROM reference.us_etf_daily
            WHERE index_entity_id = ANY(:ids) AND business_date BETWEEN :start AND :end
            ORDER BY business_date
            """
        ),
        {"ids": list(index_ids), "start": start, "end": end},
    )
    return _series_by_key(result.all())


async def load_stock_closes(
    conn: AsyncConnection, tickers: Sequence[str], start: date, end: date
) -> dict[str, pd.Series]:
    """Adjusted daily closes written by the ``backfill-ohlc`` CronJob."""
    result = await conn.execute(
        text(
            """
            SELECT symbol, trade_date, close
            FROM silver.symbol_daily_ohlc
            WHERE interval = 'd' AND close IS NOT NULL
              AND symbol = ANY(:tickers) AND trade_date BETWEEN :start AND :end
            ORDER BY trade_date
            """
        ),
        {"tickers": list(tickers), "start": start, "end": end},
    )
    return _series_by_key(result.all())


@dataclass(frozen=True)
class WeightSnapshot:
    as_of_date: date
    weight: float


async def load_weight_snapshots(
    conn: AsyncConnection,
) -> dict[tuple[UUID, UUID], list[WeightSnapshot]]:
    result = await conn.execute(
        text(
            """
            SELECT index_entity_id, symbol_entity_id, as_of_date, weight
            FROM reference.index_constituent_weight
            ORDER BY as_of_date
            """
        )
    )
    out: dict[tuple[UUID, UUID], list[WeightSnapshot]] = {}
    for index_id, symbol_id, as_of_date, weight in result.all():
        out.setdefault((index_id, symbol_id), []).append(
            WeightSnapshot(as_of_date, float(weight))
        )
    return out


@dataclass(frozen=True)
class BetaRow:
    symbol_entity_id: UUID
    as_of_date: date
    method: str
    window_days: int
    n_obs: int
    beta_global: float
    beta_global_sector: float | None
    beta_market: float
    beta_sector: float
    r2: float | None


async def upsert_symbol_betas(conn: AsyncConnection, rows: Sequence[BetaRow]) -> None:
    """One method per (symbol, as_of_date): the recomputed row replaces any other."""
    if not rows:
        return
    params = [
        {
            "symbol_entity_id": row.symbol_entity_id,
            "as_of_date": row.as_of_date,
            "method": row.method,
            "window_days": row.window_days,
            "n_obs": row.n_obs,
            "beta_global": _dec(row.beta_global),
            "beta_global_sector": _dec(row.beta_global_sector),
            "beta_market": _dec(row.beta_market),
            "beta_sector": _dec(row.beta_sector),
            "r2": _dec(row.r2),
        }
        for row in rows
    ]
    await conn.execute(
        text(
            """
            DELETE FROM gold.symbol_beta
            WHERE symbol_entity_id = :symbol_entity_id
              AND as_of_date = :as_of_date
              AND method <> :method
            """
        ),
        [
            {k: p[k] for k in ("symbol_entity_id", "as_of_date", "method")}
            for p in params
        ],
    )
    await conn.execute(
        text(
            """
            INSERT INTO gold.symbol_beta
                (symbol_entity_id, as_of_date, method, window_days, n_obs,
                 beta_global, beta_global_sector, beta_market, beta_sector, r2)
            VALUES (:symbol_entity_id, :as_of_date, :method, :window_days, :n_obs,
                    :beta_global, :beta_global_sector, :beta_market, :beta_sector, :r2)
            ON CONFLICT (symbol_entity_id, as_of_date, method) DO UPDATE
            SET window_days = EXCLUDED.window_days,
                n_obs = EXCLUDED.n_obs,
                beta_global = EXCLUDED.beta_global,
                beta_global_sector = EXCLUDED.beta_global_sector,
                beta_market = EXCLUDED.beta_market,
                beta_sector = EXCLUDED.beta_sector,
                r2 = EXCLUDED.r2
            """
        ),
        params,
    )


def _dec(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{value:.8f}")


def _series_by_key(rows: Iterable[Row[Any]]) -> dict[Any, pd.Series]:
    grouped: dict[Any, dict[datetime, float]] = {}
    for key, day, value in rows:
        grouped.setdefault(key, {})[datetime.combine(day, time())] = float(value)
    return {
        key: pd.Series(values, dtype=float).sort_index()
        for key, values in grouped.items()
    }


def index_dates(series: pd.Series) -> list[date]:
    return [stamp.date() for stamp in pd.DatetimeIndex(series.index)]

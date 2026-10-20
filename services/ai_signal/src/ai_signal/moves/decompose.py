"""Four-layer decomposition of one DAILY move (doc 22 section 11).

A DAILY move on business date D uses closes up to D: the stock close, the
same-day domestic index values, and the US ETF closes aligned with lag 1.
The beta source is the latest ``gold.symbol_beta`` row with
``as_of_date < D``. With an ``OLS_4L`` row the rolling OLS decomposition is
used (its row D uses betas fitted on data before D); otherwise ``UNIT_4L``.

``decompose_daily`` returns None while the same-day market data is not loaded
yet, so the caller stores the move with null contributions and fills them
later (``complete_pending_moves``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ai_signal.decomposition import decompose_ols_4l, decompose_unit_4l
from ai_signal.market_data.beta import SymbolHistory, symbol_layer_inputs
from ai_signal.market_data.repository import (
    MARKET_INDEX_TICKER,
    load_index_closes,
    load_stock_closes,
    load_universe,
    load_us_closes,
    load_weight_snapshots,
)
from ai_signal.market_data.trading_calendar import is_trading_day

HISTORY_DAYS = 400
US_BROAD_TICKER = "SPY"
US_DATE_SLACK = timedelta(days=7)
LAYER_COLUMNS = ("global", "market", "sector", "idio")


@dataclass(frozen=True)
class Decomposition:
    contrib_global: Decimal
    contrib_market: Decimal
    contrib_sector: Decimal
    contrib_idio: Decimal
    beta_method: str
    beta_as_of: date | None
    dominant_layer: str


@dataclass(frozen=True)
class BetaSource:
    as_of_date: date
    method: str


async def latest_beta(
    conn: AsyncConnection, symbol_id: UUID, business_date: date
) -> BetaSource | None:
    """Latest beta row strictly before ``business_date`` (OLS_4L wins ties)."""
    result = await conn.execute(
        text(
            """
            SELECT as_of_date, method FROM gold.symbol_beta
            WHERE symbol_entity_id = :id AND as_of_date < :day
            ORDER BY as_of_date DESC, (method = 'OLS_4L') DESC
            LIMIT 1
            """
        ),
        {"id": symbol_id, "day": business_date},
    )
    row = result.first()
    return BetaSource(row[0], row[1]) if row is not None else None


def _row_on(contrib: pd.DataFrame, business_date: date) -> pd.Series | None:
    if contrib.empty:
        return None
    last = contrib.iloc[-1]
    last_day = contrib.index[-1]
    if not isinstance(last_day, pd.Timestamp) or last_day.date() != business_date:
        return None
    if not all(math.isfinite(float(last[c])) for c in LAYER_COLUMNS):
        return None
    return last


def _dec(value: object) -> Decimal:
    return Decimal(str(round(float(str(value)), 8)))


async def decompose_daily(
    conn: AsyncConnection, ticker: str, business_date: date
) -> Decomposition | None:
    if not await is_trading_day(conn, business_date):
        return None
    universe = await load_universe(conn)
    symbol = next((s for s in universe.symbols if s.ticker == ticker), None)
    market = universe.domestic_by_ticker(MARKET_INDEX_TICKER)
    spy = universe.us_by_ticker(US_BROAD_TICKER)
    if symbol is None or market is None or spy is None:
        return None
    start = business_date - timedelta(days=HISTORY_DAYS)
    sector = universe.domestic_by_ticker(symbol.sector_code or "")
    index_ids = [market.entity_id] + ([sector.entity_id] if sector else [])
    us_ids = [spy.entity_id] + (
        [symbol.proxy_etf_entity_id] if symbol.proxy_etf_entity_id else []
    )
    index_closes = await load_index_closes(conn, index_ids, start, business_date)
    us_closes = await load_us_closes(conn, us_ids, start - US_DATE_SLACK, business_date)
    stock_close = (await load_stock_closes(conn, [ticker], start, business_date)).get(
        ticker
    )
    market_close = index_closes.get(market.entity_id)
    spy_close = us_closes.get(spy.entity_id)
    if stock_close is None or market_close is None or spy_close is None:
        return None
    snapshots = await load_weight_snapshots(conn)
    history = SymbolHistory(
        symbol=symbol,
        stock_close=stock_close,
        market_close=market_close,
        spy_close=spy_close,
        market_weights=snapshots.get((market.entity_id, symbol.entity_id), []),
        sector_close=index_closes.get(sector.entity_id) if sector else None,
        sector_weights=snapshots.get((sector.entity_id, symbol.entity_id), [])
        if sector
        else None,
        etf_close=us_closes.get(symbol.proxy_etf_entity_id)
        if symbol.proxy_etf_entity_id
        else None,
    )
    inputs = symbol_layer_inputs(history, business_date)
    beta = await latest_beta(conn, symbol.entity_id, business_date)
    method = "UNIT_4L"
    row = None
    if beta is not None and beta.method == "OLS_4L":
        contrib, _ = decompose_ols_4l(inputs)
        row = _row_on(contrib, business_date)
        method = "OLS_4L"
    if row is None:
        row = _row_on(decompose_unit_4l(inputs), business_date)
        method = "UNIT_4L"
    if row is None:
        return None
    return Decomposition(
        contrib_global=_dec(row["global"]),
        contrib_market=_dec(row["market"]),
        contrib_sector=_dec(row["sector"]),
        contrib_idio=_dec(row["idio"]),
        beta_method=method,
        beta_as_of=beta.as_of_date if beta is not None else None,
        dominant_layer=str(row["dominant"]).upper(),
    )
